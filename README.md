<img src="assets/app-icon.png" width="96" alt="Mochi Diary icon">

# Mochi Diary

**A cute diary buddy that reads your day.**

Write about your day for a couple of minutes and Mochi, a squishy little daifuku, tidies it into a snapshot, scores your joy, stress, social time, focus and energy, picks the animal you've been channelling and sticks stickers on the page. Over time you get trends, a calendar of animal faces, insight cards and deep dives into what lifts you and what weighs on you.

This is the Mac app. Everything is saved on your Mac, and nothing you write leaves it.

A Caffeine Not Included app.

## Download

Get the latest version from the [Releases](../../releases) page: `Mochi Diary_x.x.x_universal.dmg`. It runs on Apple Silicon and Intel Macs with macOS 11 or later.

## Install

1. Open the `.dmg` and drag **Mochi Diary** into **Applications**.
2. Open it from Applications. Mochi Diary isn't code-signed yet, so the first time macOS stops it:
   - **macOS 15 (Sequoia) or later:** click **Done**, open **System Settings → Privacy & Security**, scroll down to *"Mochi Diary" was blocked*, click **Open Anyway** and confirm.
   - **macOS 14 or earlier:** right-click (or Control-click) the app, choose **Open**, then **Open** again.
3. After that it opens normally.


## Where your diary lives

Your diary is a folder of ordinary files. **You → Show in Finder** opens it.

```
~/Library/Application Support/com.caffeinenotincluded.mochidiary/Diary/
  mochi.json            your name, notes, tone, flavour, passcode and cards
  Entries/<id>.json     one file per day
  Reports/<id>.json     deep-dive reports
  Photos/<id>.jpg       your photos at full size.
```

- Every save goes to a temporary file first and then replaces the old one, so a crash never leaves half a file.
- A file that can't be read is left exactly where it is and Mochi tells you; it's never saved over.
- **Back up everything** saves one `.json` with your days, photos and reports wherever you choose. Keep a copy somewhere safe (iCloud Drive, a USB stick), because a diary on one computer is only as safe as that computer.
- The passcode locks the app. The files themselves are plain text, so keep your Mac's login password on.



## How it's put together

| Path | What it is |
| --- | --- |
| `web/mochi-diary.html` | The whole app: the same page as Mochi Diary in Claude. It notices the desktop shell (`window.__TAURI__`) and switches to file storage, native Save dialogs and opening links in your browser. |
| `scripts/make-dist.mjs` | Wraps the page into `dist/index.html` and bundles the Mochiy Pop One and M PLUS Rounded 1c fonts (Latin slices) so it works offline. |
| `src-tauri/src/lib.rs` | The Mac side: reads and writes the diary folder, Save dialogs, Show in Finder, open links. `cargo test` covers it. |
| `src-tauri/tauri.conf.json` | App name, window size, bundle id `com.caffeinenotincluded.mochidiary`, icon, `.dmg` layout. |
| `.github/workflows/build.yml` | Builds the universal `.dmg` on GitHub. |
| `assets/app-icon.svg` | The icon source. After changing it, run `npx tauri icon assets/app-icon.png -o src-tauri/icons`. |

Fonts: Mochiy Pop One and M PLUS Rounded 1c, SIL Open Font License (copies of the licence are bundled with the app).
