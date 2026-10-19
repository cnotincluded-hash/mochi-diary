"""Desktop-mode check for Mochi Diary.

Loads dist/index.html in headless Chromium with a Python stand-in for the
Tauri commands. The stand-in writes the same folder layout as src-tauri/src/lib.rs
(mochi.json, Entries/, Reports/, Photos/), so the files on disk can be checked.
Every network request is blocked, so the app must work offline.
"""
import base64, json, os, re, shutil, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(sys.argv[1]).resolve()
DIST = ROOT / 'dist'
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''))


class FakeShell:
    """Mirrors the Rust commands."""
    TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif'}

    def __init__(self, folder):
        self.dir = Path(folder); self.dir.mkdir(parents=True, exist_ok=True)
        self.calls = []; self.saved = {}; self.fail_load = False

    def _id(self, i):
        if not re.fullmatch(r'[A-Za-z0-9_~.-]{1,80}', i or '') or i.startswith('.'):
            raise ValueError(f'refused an unusual id “{i}”.')
        return i

    def _sub(self, kind):
        return {'entry': 'Entries', 'report': 'Reports'}[kind]

    def invoke(self, cmd, args):
        args = args or {}
        self.calls.append(cmd)
        d = self.dir
        if cmd == 'load_diary':
            if self.fail_load: raise RuntimeError('permission denied')
            def folder(sub):
                out, bad = [], 0
                p = d / sub
                for f in sorted(p.glob('*.json')) if p.exists() else []:
                    try: json.loads(f.read_text()); out.append(f.read_text())
                    except Exception: bad += 1
                return out, bad
            e, b1 = folder('Entries'); r, b2 = folder('Reports')
            meta = (d / 'mochi.json').read_text() if (d / 'mochi.json').exists() else None
            warn = f'{b1 + b2} diary file couldn\'t be read and was left untouched in the diary folder.' if b1 + b2 else None
            return {'meta': meta, 'entries': e, 'reports': r, 'folder': str(d), 'warning': warn}
        if cmd == 'put_doc':
            json.loads(args['json'])
            t = d / 'mochi.json' if args['kind'] == 'meta' else d / self._sub(args['kind']) / f"{self._id(args['id'])}.json"
            t.parent.mkdir(parents=True, exist_ok=True); t.write_text(args['json']); return None
        if cmd == 'del_doc':
            t = d / self._sub(args['kind']) / f"{self._id(args['id'])}.json"
            if t.exists(): t.unlink()
            return None
        if cmd == 'put_media':
            i = self._id(args['id']); self.invoke('del_media', {'id': i}); self.calls.pop()
            m = re.match(r'data:([^;]+);base64,(.*)$', args['data'], re.S)
            p = d / 'Photos'; p.mkdir(parents=True, exist_ok=True)
            if m and m.group(1) in self.TYPES:
                (p / f"{i}.{self.TYPES[m.group(1)]}").write_bytes(base64.b64decode(m.group(2)))
            else:
                (p / f'{i}.txt').write_text(args['data'])
            return None
        if cmd == 'get_media':
            i = self._id(args['id'])
            for mime, ext in self.TYPES.items():
                f = d / 'Photos' / f'{i}.{ext}'
                if f.exists(): return f'data:{mime};base64,' + base64.b64encode(f.read_bytes()).decode()
            f = d / 'Photos' / f'{i}.txt'
            return f.read_text() if f.exists() else None
        if cmd == 'del_media':
            i = self._id(args['id'])
            for f in (d / 'Photos').glob(f'{i}.*') if (d / 'Photos').exists() else []: f.unlink()
            return None
        if cmd == 'clear_diary':
            for sub in ('Entries', 'Reports', 'Photos'): shutil.rmtree(d / sub, ignore_errors=True)
            return None
        if cmd == 'save_export':
            self.saved[args['filename']] = args['contents']; return args['filename']
        if cmd in ('show_diary_folder', 'open_link'):
            self.calls[-1] = (cmd, args); return None
        raise ValueError('unknown command ' + cmd)


BRIDGE = """
window.__TAURI__ = { core: { invoke: (cmd, args) => window.__shell(cmd, args || {}).then(r => {
  if (r && r.error !== undefined) throw r.error; return r.ok; }) } };
"""


def make_jpeg(path):
    from PIL import Image
    Image.new('RGB', (1800, 1200), (255, 180, 200)).save(path, 'JPEG')


def entries(d): return sorted((Path(d) / 'Entries').glob('*.json')) if (Path(d) / 'Entries').exists() else []


def run():
    tmp = Path(tempfile.mkdtemp(prefix='mochi-desk-'))
    shell = FakeShell(tmp / 'Diary')
    photo = tmp / 'photo.jpg'; make_jpeg(photo)
    errors, external = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 520, 'height': 900})

        def route(r):
            if r.request.url.startswith('file://'): r.continue_()
            else: external.append(r.request.url); r.abort()
        ctx.route('**/*', route)

        def shell_fn(cmd, args):
            try: return {'ok': shell.invoke(cmd, args)}
            except Exception as e: return {'error': str(e)}
        ctx.expose_function('__shell', shell_fn)
        ctx.add_init_script(BRIDGE)

        page = ctx.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        url = (DIST / 'index.html').as_uri()

        def tap(act):
            # clicks the newest matching control (the one on the top sheet)
            n = page.evaluate("a => { const l = [...document.querySelectorAll(`[data-act='${a}']`)]; const el = l[l.length - 1]; if (el) el.click(); return l.length; }", act)
            if not n: raise AssertionError('no control ' + act)

        def load():
            page.goto(url); page.wait_for_function("!document.getElementById('splash')", timeout=10000)

        load()
        check('First launch shows the welcome sheet', page.locator('#w-name').is_visible())
        check('Welcome says the diary is saved on this Mac', 'saved on this Mac' in page.inner_text('body'))
        fonts = page.evaluate("""async () => { await document.fonts.ready;
          return [document.fonts.check('400 20px "Mochiy Pop One"', 'Hi'), document.fonts.check('700 16px "M PLUS Rounded 1c"', 'Hi')]; }""")
        page.evaluate("""() => new Promise(r => { const t = document.createElement('span'); t.style.font = '400 20px "Mochiy Pop One"'; t.textContent = 'Mochi'; document.body.append(t); document.fonts.load('400 20px "Mochiy Pop One"').then(() => r()); })""")
        loaded = page.evaluate("""() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family)""")
        check('Bundled fonts load with no network', any('Mochiy' in f for f in loaded) and any('M PLUS' in f for f in loaded), str(loaded[:4]))
        check('Nothing is fetched from the internet', not external, str(external[:3]))

        page.fill('#w-name', 'Sophia'); page.click('[data-act="welcome-go"]'); page.wait_for_timeout(500)
        meta = json.loads((shell.dir / 'mochi.json').read_text())
        check('Name saved to mochi.json', meta.get('name') == 'Sophia' and meta.get('welcomed'))

        # write a day with a photo
        page.click('[data-act="new"]'); page.wait_for_selector('#c-ta')
        free = page.locator('[data-act="c-mode"][data-m="free"]')
        if free.count(): free.click(); page.wait_for_selector('#c-ta')
        page.fill('#c-ta', 'Had ramen with Priya after work, laughed a lot, then a calm walk home in the rain. Tired but happy.')
        page.set_input_files('#c-file', str(photo)); page.wait_for_timeout(800)
        page.click('#c-save'); page.wait_for_timeout(1500)
        files = entries(shell.dir)
        check('Saving a day writes Entries/<id>.json', len(files) == 1)
        ent = json.loads(files[0].read_text()) if files else {}
        check('The day has a quick read', (ent.get('ai') or {}).get('src') == 'quick')
        pid = (ent.get('photo') or {}).get('id')
        jpg = shell.dir / 'Photos' / f'{pid}.jpg'
        check('Photo stored as a real JPEG in Photos/', jpg.exists() and jpg.read_bytes()[:2] == b'\xff\xd8')
        if jpg.exists():
            from PIL import Image
            check('Photo scaled to 1400 px', max(Image.open(jpg).size) == 1400, str(Image.open(jpg).size))

        # reload: still there, photo loads back from the file
        load()
        check('After a restart the day is still there', page.locator('.ecard').count() >= 1 and not page.locator('#w-name').count())
        page.locator('[data-act="open-entry"]').first.click(); page.wait_for_timeout(1200)
        big = page.evaluate("""() => [...document.querySelectorAll('img')].some(i => i.naturalWidth >= 1400)""")
        check('Full-size photo loads back from the file', big)
        page.keyboard.press('Escape'); page.wait_for_timeout(400)

        # You sheet
        page.click('[data-act="you"]'); page.wait_for_timeout(500)
        body = page.inner_text('body')
        check('You sheet says the diary is saved as files on this Mac', 'Saved as files on this Mac' in body)
        check('Smart reads line explains quick reads on the Mac', 'right here on your Mac' in body)
        check('Restore mentions backups from Claude', 'from Mochi Diary in Claude' in body)
        page.click('[data-act="show-folder"]'); page.wait_for_timeout(200)
        check('Show in Finder calls the shell', any(c[0] == 'show_diary_folder' for c in shell.calls if isinstance(c, tuple)))

        page.click('[data-act="export-json"]'); page.wait_for_timeout(1500)
        bk = next((v for k, v in shell.saved.items() if k.endswith('.json')), None)
        okb = False
        if bk:
            d = json.loads(bk); okb = d.get('app') == 'mochi-diary' and len(d['entries']) == 1 and pid in d.get('media', {})
        check('Back up goes through the Save dialog with the day and photo', okb)
        page.click('[data-act="export-md"]'); page.wait_for_timeout(800)
        md = next((v for k, v in shell.saved.items() if k.endswith('.md')), '')
        check('Export as text goes through the Save dialog', 'ramen' in md)

        # helpline link opens outside
        before = page.url
        page.evaluate("""() => { const a = [...document.querySelectorAll('a[href^="http"]')][0]; a && a.click(); }""")
        page.wait_for_timeout(300)
        opened = [c for c in shell.calls if isinstance(c, tuple) and c[0] == 'open_link']
        check('Links open in the browser, not the app window', opened and page.url == before, str(opened))

        # examples on and off
        page.click('[data-act="add-examples"]'); page.wait_for_timeout(1500)
        check('Example days are written as files', len(entries(shell.dir)) == 11, str(len(entries(shell.dir))))
        tap('clear-examples'); page.wait_for_timeout(1500)
        check('Removing examples deletes their files', len(entries(shell.dir)) == 1, str(len(entries(shell.dir))))

        # passcode
        if not page.locator('[data-act="lock-toggle"]').count():
            tap('you'); page.wait_for_timeout(400)
        tap('lock-toggle'); page.wait_for_timeout(400)
        for _ in range(2):
            for dgt in '2468':
                page.click(f'[data-act="pin"][data-d="{dgt}"]'); page.wait_for_timeout(60)
            page.wait_for_timeout(400)
        meta = json.loads((shell.dir / 'mochi.json').read_text())
        check('Passcode hash saved in mochi.json', bool(meta.get('pinHash')))
        load()
        check('After a restart the diary is locked', page.locator('#lock').is_visible())
        for dgt in '2468':
            page.click(f'#lock [data-act="pin"][data-d="{dgt}"]'); page.wait_for_timeout(60)
        page.wait_for_timeout(500)
        check('Right code unlocks', not page.locator('#lock').is_visible())
        page.evaluate("() => { window.dispatchEvent(new Event('blur')); }"); page.wait_for_timeout(50)
        page.evaluate("() => { window.dispatchEvent(new Event('focus')); }"); page.wait_for_timeout(300)
        check('Switching to another app and back locks it again (Right away)', page.locator('#lock').is_visible())
        for dgt in '2468':
            page.click(f'#lock [data-act="pin"][data-d="{dgt}"]'); page.wait_for_timeout(60)
        page.wait_for_timeout(500)

        # restore the backup into an empty diary after erase
        tap('you'); page.wait_for_timeout(400)
        tap('erase'); page.wait_for_timeout(400)
        page.evaluate("() => { const l = [...document.querySelectorAll('.alertbox [data-r=\"1\"]')]; l[l.length - 1].click(); }"); page.wait_for_timeout(1200)
        check('Erase removes the days and photos', not entries(shell.dir) and not (shell.dir / 'Photos').exists())
        check('Erase shows the welcome again', page.locator('#w-name').count() == 1)
        tap('welcome-go'); page.wait_for_timeout(400)
        bkf = tmp / 'backup.json'; bkf.write_text(bk or '{}')
        tap('you'); page.wait_for_timeout(400)
        page.set_input_files('#y-import', str(bkf)); page.wait_for_timeout(1500)
        check('Restoring the backup brings the day and photo back as files', len(entries(shell.dir)) == 1 and jpg.exists())

        # damaged file is reported and left alone
        (shell.dir / 'Entries' / 'broken.json').write_text('{oops')
        load(); page.wait_for_timeout(1200)
        check('A damaged file is reported and left untouched', "couldn't be read" in page.inner_text('body') and (shell.dir / 'Entries' / 'broken.json').read_text() == '{oops')
        (shell.dir / 'Entries' / 'broken.json').unlink()

        # folder unavailable
        shell.fail_load = True
        load(); page.wait_for_timeout(900)
        check("If the folder can't open, the app says so and still runs", "Couldn't open your diary folder" in page.inner_text('body'))
        shell.fail_load = False

        check('No page errors in desktop mode', not errors, '; '.join(errors[:3]))
        ctx.close()

        # browser mode is unchanged
        ctx2 = browser.new_context(viewport={'width': 390, 'height': 844})
        ctx2.route('**/*', lambda r: r.continue_() if r.request.url.startswith('file://') else r.abort())
        pg = ctx2.new_page(); errs2 = []
        pg.on('pageerror', lambda e: errs2.append(str(e)))
        pg.goto(url); pg.wait_for_function("!document.getElementById('splash')", timeout=10000)
        check('Outside the app it still uses browser storage', 'saved in this browser' in pg.inner_text('body'))
        check('No page errors in browser mode', not errs2, '; '.join(errs2[:3]))
        browser.close()
    shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(ok for _, ok in results)
    print(f'\n{passed} of {len(results)} pass')
    return passed == len(results)


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
