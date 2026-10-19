//! Mochi Diary desktop shell.
//!
//! The whole app is the page in `dist/`. This side only does what a web page
//! can't: keep the diary as ordinary files on the Mac, show native Save
//! dialogs, open links in the browser and reveal the diary in Finder.
//!
//! Diary folder (inside the app's data folder):
//!   mochi.json            settings, name, notes, tone, flavour, passcode hash, cards
//!   Entries/<id>.json     one file per day
//!   Reports/<id>.json     deep-dive reports
//!   Photos/<id>.jpg       full-size photos (thumbnails stay inside the entry)

use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

const DIARY_DIR: &str = "Diary";
const META_FILE: &str = "mochi.json";
const ENTRIES: &str = "Entries";
const REPORTS: &str = "Reports";
const PHOTOS: &str = "Photos";

#[derive(Serialize)]
struct Loaded {
    meta: Option<String>,
    entries: Vec<String>,
    reports: Vec<String>,
    folder: String,
    warning: Option<String>,
}

fn err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Ids come from the page; only plain names are allowed so nothing can reach outside the folder.
fn check_id(id: &str) -> Result<&str, String> {
    let ok = !id.is_empty()
        && id.len() <= 80
        && !id.starts_with('.')
        && id.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~'));
    if ok {
        Ok(id)
    } else {
        Err(format!("refused an unusual id “{id}”."))
    }
}

fn sub_for(kind: &str) -> Result<&'static str, String> {
    match kind {
        "entry" => Ok(ENTRIES),
        "report" => Ok(REPORTS),
        other => Err(format!("unknown kind “{other}”.")),
    }
}

/// Write to a temporary file, then rename over the target, so a crash or a
/// full disk never leaves a half-written file behind.
fn write_atomic(target: &Path, bytes: &[u8]) -> Result<(), String> {
    if let Some(dir) = target.parent() {
        fs::create_dir_all(dir).map_err(err)?;
    }
    let mut name = target.file_name().map(|n| n.to_os_string()).unwrap_or_default();
    name.push(".tmp");
    let tmp = target.with_file_name(name);
    fs::write(&tmp, bytes).map_err(|e| format!("couldn’t write {}: {e}", tmp.display()))?;
    fs::rename(&tmp, target).map_err(|e| format!("couldn’t replace {}: {e}", target.display()))
}

fn remove_if_there(p: &Path) -> Result<(), String> {
    match fs::remove_file(p) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("couldn’t delete {}: {e}", p.display())),
    }
}

fn diary_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(err)?.join(DIARY_DIR);
    fs::create_dir_all(&dir).map_err(err)?;
    Ok(dir)
}

/// Reads every `.json` file in a folder. Files that aren't valid JSON are left
/// exactly where they are and counted, never overwritten.
fn read_folder(dir: &Path, skipped: &mut Vec<String>) -> Result<Vec<String>, String> {
    let mut out = Vec::new();
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(out),
        Err(e) => return Err(format!("couldn’t read {}: {e}", dir.display())),
    };
    let mut paths: Vec<PathBuf> = rd
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().and_then(|x| x.to_str()) == Some("json"))
        .collect();
    paths.sort();
    for p in paths {
        match fs::read_to_string(&p) {
            Ok(s) if serde_json::from_str::<serde_json::Value>(&s).is_ok() => out.push(s),
            _ => skipped.push(p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()),
        }
    }
    Ok(out)
}

fn load_from(dir: &Path) -> Result<Loaded, String> {
    let mut skipped = Vec::new();
    let mut warning = None;
    let meta_path = dir.join(META_FILE);
    let meta = match fs::read_to_string(&meta_path) {
        Ok(s) if serde_json::from_str::<serde_json::Value>(&s).is_ok() => Some(s),
        Ok(_) => {
            // Keep the unreadable settings aside instead of saving over them.
            let aside = dir.join(format!("mochi-unreadable-{}.json", now_secs()));
            fs::rename(&meta_path, &aside).map_err(err)?;
            warning = Some(format!(
                "Mochi's settings file couldn't be read, so it was moved aside as {}. Your days are safe.",
                aside.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
            ));
            None
        }
        Err(e) if e.kind() == ErrorKind::NotFound => None,
        Err(e) => return Err(format!("couldn’t read {}: {e}", meta_path.display())),
    };
    let entries = read_folder(&dir.join(ENTRIES), &mut skipped)?;
    let reports = read_folder(&dir.join(REPORTS), &mut skipped)?;
    if !skipped.is_empty() && warning.is_none() {
        warning = Some(format!(
            "{} diary file{} couldn't be read and {} left untouched in the diary folder.",
            skipped.len(),
            if skipped.len() == 1 { "" } else { "s" },
            if skipped.len() == 1 { "was" } else { "were" }
        ));
    }
    Ok(Loaded { meta, entries, reports, folder: dir.to_string_lossy().into_owned(), warning })
}

fn put_doc_in(dir: &Path, kind: &str, id: &str, json: &str) -> Result<(), String> {
    serde_json::from_str::<serde_json::Value>(json).map_err(|e| format!("refused to save damaged data: {e}"))?;
    let target = if kind == "meta" {
        dir.join(META_FILE)
    } else {
        dir.join(sub_for(kind)?).join(format!("{}.json", check_id(id)?))
    };
    write_atomic(&target, json.as_bytes())
}

fn del_doc_in(dir: &Path, kind: &str, id: &str) -> Result<(), String> {
    remove_if_there(&dir.join(sub_for(kind)?).join(format!("{}.json", check_id(id)?)))
}

const PHOTO_TYPES: [(&str, &str); 4] =
    [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp"), ("image/gif", "gif")];

/// Photos arrive as data URLs. Images are stored as real image files; anything
/// else is kept as text so it still round-trips.
fn put_media_in(dir: &Path, id: &str, data_url: &str) -> Result<(), String> {
    let id = check_id(id)?;
    let photos = dir.join(PHOTOS);
    del_media_in(dir, id)?;
    if let Some(rest) = data_url.strip_prefix("data:") {
        if let Some((head, body)) = rest.split_once(";base64,") {
            if let Some((_, ext)) = PHOTO_TYPES.iter().find(|(m, _)| *m == head) {
                let bytes = B64.decode(body.trim()).map_err(|e| format!("photo data was damaged: {e}"))?;
                return write_atomic(&photos.join(format!("{id}.{ext}")), &bytes);
            }
        }
    }
    write_atomic(&photos.join(format!("{id}.txt")), data_url.as_bytes())
}

fn get_media_in(dir: &Path, id: &str) -> Result<Option<String>, String> {
    let id = check_id(id)?;
    let photos = dir.join(PHOTOS);
    for (mime, ext) in PHOTO_TYPES {
        match fs::read(photos.join(format!("{id}.{ext}"))) {
            Ok(bytes) => return Ok(Some(format!("data:{mime};base64,{}", B64.encode(bytes)))),
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(err(e)),
        }
    }
    match fs::read_to_string(photos.join(format!("{id}.txt"))) {
        Ok(s) => Ok(Some(s)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(err(e)),
    }
}

fn del_media_in(dir: &Path, id: &str) -> Result<(), String> {
    let id = check_id(id)?;
    let photos = dir.join(PHOTOS);
    for (_, ext) in PHOTO_TYPES {
        remove_if_there(&photos.join(format!("{id}.{ext}")))?;
    }
    remove_if_there(&photos.join(format!("{id}.txt")))
}

/// "Erase my diary": days, photos and reports go; mochi.json is rewritten by the page.
fn clear_in(dir: &Path) -> Result<(), String> {
    for sub in [ENTRIES, REPORTS, PHOTOS] {
        match fs::remove_dir_all(dir.join(sub)) {
            Ok(()) => {}
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(format!("couldn’t erase {sub}: {e}")),
        }
    }
    Ok(())
}

#[tauri::command]
fn load_diary(app: AppHandle) -> Result<Loaded, String> {
    load_from(&diary_dir(&app)?)
}

#[tauri::command]
fn put_doc(app: AppHandle, kind: String, id: String, json: String) -> Result<(), String> {
    put_doc_in(&diary_dir(&app)?, &kind, &id, &json)
}

#[tauri::command]
fn del_doc(app: AppHandle, kind: String, id: String) -> Result<(), String> {
    del_doc_in(&diary_dir(&app)?, &kind, &id)
}

#[tauri::command]
fn put_media(app: AppHandle, id: String, data: String) -> Result<(), String> {
    put_media_in(&diary_dir(&app)?, &id, &data)
}

#[tauri::command]
fn get_media(app: AppHandle, id: String) -> Result<Option<String>, String> {
    get_media_in(&diary_dir(&app)?, &id)
}

#[tauri::command]
fn del_media(app: AppHandle, id: String) -> Result<(), String> {
    del_media_in(&diary_dir(&app)?, &id)
}

#[tauri::command]
fn clear_diary(app: AppHandle) -> Result<(), String> {
    clear_in(&diary_dir(&app)?)
}

#[tauri::command]
fn show_diary_folder(app: AppHandle) -> Result<(), String> {
    let dir = diary_dir(&app)?;
    let target = if dir.join(META_FILE).exists() { dir.join(META_FILE) } else { dir };
    app.opener().reveal_item_in_dir(target).map_err(err)
}

#[tauri::command]
fn open_link(app: AppHandle, url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://") || url.starts_with("mailto:")) {
        return Err("only web links can be opened.".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(err)
}

#[tauri::command]
async fn save_export(app: AppHandle, filename: String, contents: String) -> Result<Option<String>, String> {
    let ext = Path::new(&filename).extension().and_then(|e| e.to_str()).unwrap_or("").to_string();
    let mut dialog = app.dialog().file().set_file_name(&filename);
    if let Some(docs) = app.path().document_dir().ok().filter(|d| d.is_dir()) {
        dialog = dialog.set_directory(docs);
    }
    if !ext.is_empty() {
        dialog = dialog.add_filter(ext.to_uppercase(), &[ext.as_str()]);
    }
    match dialog.blocking_save_file() {
        None => Ok(None),
        Some(choice) => {
            let path = choice.as_path().ok_or_else(|| "that location can’t be saved to.".to_string())?.to_path_buf();
            fs::write(&path, contents).map_err(|e| format!("couldn’t write {}: {e}", path.display()))?;
            Ok(Some(path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()))
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            load_diary,
            put_doc,
            del_doc,
            put_media,
            get_media,
            del_media,
            clear_diary,
            show_diary_folder,
            open_link,
            save_export
        ])
        .run(tauri::generate_context!())
        .expect("error while running Mochi Diary");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mochi-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn ids_stay_inside_the_folder() {
        assert!(check_id("mk3x9a2b4c").is_ok());
        assert!(check_id("ex12ab3c").is_ok());
        assert!(check_id("joy").is_ok());
        for bad in ["", "../x", "a/b", "..", ".hidden", "a\\b", "x y"] {
            assert!(check_id(bad).is_err(), "{bad} should be refused");
        }
        let dir = temp("ids");
        assert!(put_doc_in(&dir, "entry", "../escape", "{}").is_err());
        assert!(put_doc_in(&dir, "secret", "a", "{}").is_err());
        assert!(!dir.parent().unwrap().join("escape.json").exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn docs_round_trip() {
        let dir = temp("docs");
        let empty = load_from(&dir).unwrap();
        assert!(empty.meta.is_none() && empty.entries.is_empty() && empty.warning.is_none());
        put_doc_in(&dir, "meta", "", r#"{"name":"Sophia"}"#).unwrap();
        put_doc_in(&dir, "entry", "a1", r#"{"id":"a1","text":"one"}"#).unwrap();
        put_doc_in(&dir, "entry", "b2", r#"{"id":"b2","text":"two"}"#).unwrap();
        put_doc_in(&dir, "entry", "a1", r#"{"id":"a1","text":"one, edited"}"#).unwrap();
        put_doc_in(&dir, "report", "joy", r#"{"id":"joy"}"#).unwrap();
        assert!(put_doc_in(&dir, "entry", "c3", "not json").is_err());
        let l = load_from(&dir).unwrap();
        assert_eq!(l.meta.as_deref(), Some(r#"{"name":"Sophia"}"#));
        assert_eq!(l.entries.len(), 2);
        assert!(l.entries[0].contains("one, edited"));
        assert_eq!(l.reports.len(), 1);
        assert!(!dir.join(ENTRIES).join("a1.json.tmp").exists());
        del_doc_in(&dir, "entry", "b2").unwrap();
        del_doc_in(&dir, "entry", "b2").unwrap(); // deleting twice is fine
        assert_eq!(load_from(&dir).unwrap().entries.len(), 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn unreadable_files_are_kept() {
        let dir = temp("bad");
        put_doc_in(&dir, "entry", "ok", r#"{"id":"ok"}"#).unwrap();
        fs::write(dir.join(ENTRIES).join("broken.json"), "{oops").unwrap();
        let l = load_from(&dir).unwrap();
        assert_eq!(l.entries.len(), 1);
        assert!(l.warning.unwrap().contains("1 diary file"));
        assert_eq!(fs::read_to_string(dir.join(ENTRIES).join("broken.json")).unwrap(), "{oops");

        fs::write(dir.join(META_FILE), "nope").unwrap();
        let l = load_from(&dir).unwrap();
        assert!(l.meta.is_none());
        assert!(l.warning.unwrap().contains("moved aside"));
        assert!(!dir.join(META_FILE).exists());
        let aside = fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()).any(|e| {
            e.file_name().to_string_lossy().starts_with("mochi-unreadable-")
        });
        assert!(aside);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn photos_are_real_image_files() {
        let dir = temp("media");
        let jpeg = [0xFFu8, 0xD8, 0xFF, 0xE0, 1, 2, 3, 4];
        let url = format!("data:image/jpeg;base64,{}", B64.encode(jpeg));
        put_media_in(&dir, "p1", &url).unwrap();
        assert_eq!(fs::read(dir.join(PHOTOS).join("p1.jpg")).unwrap(), jpeg);
        assert_eq!(get_media_in(&dir, "p1").unwrap().as_deref(), Some(url.as_str()));

        let png = format!("data:image/png;base64,{}", B64.encode([0x89u8, b'P', b'N', b'G']));
        put_media_in(&dir, "p1", &png).unwrap(); // replacing swaps the file type
        assert!(!dir.join(PHOTOS).join("p1.jpg").exists());
        assert_eq!(get_media_in(&dir, "p1").unwrap().as_deref(), Some(png.as_str()));

        put_media_in(&dir, "odd", "data:text/plain,hello").unwrap();
        assert_eq!(get_media_in(&dir, "odd").unwrap().as_deref(), Some("data:text/plain,hello"));
        assert_eq!(get_media_in(&dir, "missing").unwrap(), None);
        assert!(put_media_in(&dir, "p2", "data:image/jpeg;base64,!!!").is_err());

        del_media_in(&dir, "p1").unwrap();
        assert_eq!(get_media_in(&dir, "p1").unwrap(), None);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn erase_clears_days_but_not_settings() {
        let dir = temp("erase");
        put_doc_in(&dir, "meta", "", "{}").unwrap();
        put_doc_in(&dir, "entry", "a", "{}").unwrap();
        put_doc_in(&dir, "report", "joy", "{}").unwrap();
        put_media_in(&dir, "a", "data:image/png;base64,AAAA").unwrap();
        clear_in(&dir).unwrap();
        clear_in(&dir).unwrap();
        let l = load_from(&dir).unwrap();
        assert!(l.entries.is_empty() && l.reports.is_empty());
        assert!(l.meta.is_some());
        assert!(!dir.join(PHOTOS).exists());
        let _ = fs::remove_dir_all(&dir);
    }
}
