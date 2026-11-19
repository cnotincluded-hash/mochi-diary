"""Feelings check for Mochi Diary.

Part 1 runs the on-device reader on sample entries and checks the feelings, causes
and day shapes it finds. Part 2 drives the desktop app (with the same stand-in shell
as desktop_test.py): writing a day with live "Mochi senses", correcting feelings,
the Trends feelings card, pattern cards, deep dives, and re-reading days saved by
the older reader.

    python tests/feelings_test.py .            (after: node scripts/make-dist.mjs)
    python tests/feelings_test.py . --shots    (also saves screenshots to tests/shots)
"""
import json, os, shutil, sys, tempfile
from datetime import date, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
_argv = sys.argv
sys.argv = [_argv[0], _argv[1] if len(_argv) > 1 else '.', 'mac']  # desktop_test reads its own arguments
import desktop_test as D  # noqa: E402
sys.argv = _argv

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
SHOTS = '--shots' in sys.argv
results = D.results


def check(name, ok, detail=''):
    D.check(name, ok, detail)


# entry -> (feelings that must be found, feelings that must not, expected arc types or None, cause words)
CASES = [
    ("Slept badly and woke up tired. The meeting this morning was so stressful and I was anxious about the deadline. But dinner with Sam tonight was lovely and I feel much calmer now.",
     ['tired', 'stressed', 'anxious', 'calm'], [], ['rise'], {'anxious': 'deadline', 'stressed': 'meeting'}),
    ("I'm not happy with how the presentation went. Kind of embarrassed tbh.",
     ['disappointed', 'embarrassed'], ['happy'], None, {'disappointed': 'presentation'}),
    ("Had lunch with Priya, couldn't stop laughing!! Felt really understood. Then got home and nobody texted back, felt a bit lonely.",
     ['amused', 'connected', 'lonely'], [], ['fall', 'mixed'], {'lonely': 'nobody texted back', 'amused': 'Priya'}),
    ("Mum was really stressed about her appointment, I tried to help. I'm worried about her.",
     ['anxious'], ['stressed'], None, {'anxious': 'Mum'}),
    ("I miss my dad so much today. Found old photos and cried. Grateful for the memories though.",
     ['sad', 'grateful'], [], ['rise', 'mixed'], {'sad': 'dad'}),
    ("Tom was rude to me at work and I'm still fuming. Seeing Maya after cheered me up a little.",
     ['angry', 'annoyed', 'happy'], [], None, {'annoyed': 'Tom', 'happy': 'Maya'}),
    ("Am I happy? I don't even know anymore.", [], ['happy'], None, {}),
    ("My back hurt all day but the yoga class was relaxing.", ['calm'], ['hurt'], None, {'calm': 'yoga'}),
    ("Not stressed at all today, which is new.", ['calm'], ['stressed'], None, {}),
    ("SO PROUD of myself!! Finally finished the essay 🎉", ['proud', 'excited'], [], None, {}),
    ("Can't be bothered with anything. Feeling flat and empty.", ['flat'], [], None, {}),
    ("Waking up: Couldn't sleep, kept overthinking.\n\nMorning: Dreading the interview.\n\nAfternoon: The interview went well actually! Relieved.\n\nEvening: Pizza with my sister, felt so happy.",
     ['anxious', 'relieved', 'happy'], [], ['rise'], {'anxious': 'interview', 'happy': 'sister'}),
]


def iso(d): return d.isoformat()


def old_entry(i, day, text, src='quick'):
    """A day saved by the older reader (no feelings)."""
    ai = {'title': text.split('.')[0][:38], 'snapshot': text[:520], 'scores': {'joy': 5, 'stress': 5, 'social': 5, 'productivity': 5, 'energy': 5},
          'factors': [], 'animal': 'cat', 'animalLine': '', 'stickers': [], 'people': [], 'areas': [], 'reply': 'Thanks for telling me.', 'care': False, 'src': src, 'at': 1}
    return {'id': f'old{i:02d}', 'day': day, 'at': 1, 'mode': 'free', 'parts': None, 'text': text, 'photo': None, 'ai': ai,
            'status': 'ready', 'rating': 0, 'note': '', 'example': False, 'created': 1, 'updated': 1}


OLD_DAYS = [
    "Anxious about the deadline all morning. Dinner with Sam was lovely though, felt so much calmer.",
    "Lunch with Priya, laughed for ages. Felt really understood.",
    "Slept badly, kept waking up thinking about the deadline. Stressed and tired.",
    "Coffee with Priya, laughed so much. Grateful for her.",
    "Anxious about the deadline again. Snapped at Sam, feel bad about it.",
    "Walked Biscuit in the park, so peaceful. Called Mum, felt connected.",
    "The presentation went well, proud of myself! Celebrated with Sam.",
]


def run():
    tmp = Path(tempfile.mkdtemp(prefix='mochi-feel-'))
    shell = D.FakeShell(tmp / 'Diary')
    errors = []
    shots = ROOT / 'tests' / 'shots'
    if SHOTS: shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 412, 'height': 900}, user_agent=D.UA, device_scale_factor=2)
        ctx.route('**/*', lambda r: r.continue_() if r.request.url.startswith('file://') else r.abort())
        def shell_fn(cmd, args):
            try: return {'ok': shell.invoke(cmd, args)}
            except Exception as e: return {'error': str(e)}
        ctx.expose_function('__shell', shell_fn)
        ctx.add_init_script(D.BRIDGE)
        page = ctx.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        url = (ROOT / 'dist' / 'index.html').as_uri()

        def tap(sel):
            n = page.evaluate("s => { const l = [...document.querySelectorAll(s)]; const el = l[l.length - 1]; if (el) el.click(); return l.length; }", sel)
            if not n: raise AssertionError('no control ' + sel)
            page.wait_for_timeout(250)

        def load():
            page.goto(url); page.wait_for_function("!document.getElementById('splash')", timeout=10000)

        def shot(name):
            if SHOTS: page.screenshot(path=str(shots / f'{name}.png'))

        # ---------- part 1: the reader on its own ----------
        load()
        reads = page.evaluate("cases => cases.map(t => window.__mochiRead(t))", [c[0] for c in CASES])
        for (text, must, mustnt, arcs, causes), r in zip(CASES, reads):
            ids = [f['id'] for f in r['feelings']]
            label = text[:46].replace('\n', ' ')
            check(f'Finds {", ".join(must) or "no feelings"}: "{label}…"', all(m in ids for m in must) and not any(x in ids for x in mustnt), str(ids))
            if arcs: check(f'Day shape is {"/".join(arcs)}: "{label}…"', r['arc']['type'] in arcs, r['arc']['type'])
            for fid, word in causes.items():
                f = next((x for x in r['feelings'] if x['id'] == fid), None)
                check(f'{fid.capitalize()} is about "{word}"', f is not None and word.lower() in (f.get('cause') or '').lower(), str(f))

        # ---------- part 2: the app ----------
        # older days in the folder, saved before the feelings reader
        today = date.today()
        (shell.dir / 'Entries').mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(OLD_DAYS):
            day = iso(today - timedelta(days=len(OLD_DAYS) - i))
            (shell.dir / 'Entries' / f'old{i:02d}.json').write_text(json.dumps(old_entry(i, day, t)))
        (shell.dir / 'mochi.json').write_text(json.dumps({'name': 'Sophia', 'welcomed': True, 'notes': 'Sam is my partner, Priya is my best friend, Biscuit is our dog.'}))
        load()
        page.wait_for_timeout(1200)
        saved = [json.loads(f.read_text()) for f in D.entries(shell.dir)]
        check('Older days are re-read with feelings on launch', all(e['ai'].get('v') == 2 and e['ai'].get('feelings') for e in saved), str([len(e['ai'].get('feelings') or []) for e in saved]))
        check('Mochi says it re-read them', 're-read 7 days' in page.inner_text('#toast'), page.inner_text('#toast'))
        check('Day cards show feeling chips', page.locator('.ecard .ec-feel .fmini').count() >= 7)
        shot('01-diary')

        # write a day: Mochi senses feelings while you type
        tap('.fab')
        tap("[data-act='c-mode'][data-m='free']")
        page.fill('#c-ta', 'Woke up anxious about the deadline. ')
        page.type('#c-ta', 'Lunch with Priya was lovely and I felt so relieved after.')
        page.wait_for_timeout(600)
        sensed = page.locator('#c-senses .fpick.on').all_inner_texts()
        check('Mochi senses feelings while you write', any('Anxious' in s for s in sensed) and any('Relieved' in s for s in sensed), str(sensed))
        shot('02-composer')
        tap("#c-senses [data-act='c-feel-drop'][data-id='anxious']")
        tap("#c-senses [data-act='c-feel-add']")
        tap("[data-pick='grateful']")
        sensed = page.locator('#c-senses .fpick.on').all_inner_texts()
        check('You can drop and add sensed feelings', not any('Anxious' in s for s in sensed) and any('Grateful' in s for s in sensed), str(sensed))
        tap('#c-save'); page.wait_for_timeout(500)
        newest = max(D.entries(shell.dir), key=lambda f: f.stat().st_mtime)
        e = json.loads(newest.read_text())
        ids = [f['id'] for f in e['ai']['feelings']]
        check('Saved day keeps your corrections', 'anxious' not in ids and 'grateful' in ids and 'relieved' in ids, str(ids))
        check('Corrections are stored with the day', e.get('feelFix') and 'anxious' in e['feelFix']['drop'], str(e.get('feelFix')))
        check("Today's vibe shows how you felt", page.locator('.vibe .vfeel .fmini').count() >= 2)
        shot('03-vibe')

        # the day view: How you felt, edits
        page.locator('.vibe').click(); page.wait_for_timeout(500)
        check('Day view has a How you felt card', page.locator('.feelcard .frow').count() >= 2)
        before = page.locator(".frow:has([data-id='relieved']) .str").inner_text()
        tap(".fname[data-id='relieved']")
        after = page.locator(".frow:has([data-id='relieved']) .str").inner_text()
        check('Tapping a feeling changes how strong it was', before != after, f'{before} -> {after}')
        tap(".fx[data-act='f-drop'][data-id='grateful']")
        check('× removes a feeling', page.locator(".frow [data-id='grateful']").count() == 0)
        shot('04-day')
        page.keyboard.press('Escape'); page.wait_for_timeout(300)

        # a day with a clear shape shows the arc chart
        page.locator('.ecard', has_text='deadline').first.click(); page.wait_for_timeout(500)
        check('A day that turned around shows its shape', page.locator('.feelcard .arcsvg').count() == 1 and 'better finish' in page.inner_text('.feelcard'), page.inner_text('.feelcard')[:120])
        shot('05-arc')
        page.keyboard.press('Escape'); page.wait_for_timeout(300)

        # trends
        tap(".tab[data-tab='trends']")
        tap("[data-act='range'][data-r='month']")
        check('Trends has a Feelings card', page.locator('.fcard .fbar').count() >= 4)
        tap(".fbar[data-id='anxious']")
        det = page.inner_text('.fdet') if page.locator('.fdet').count() else ''
        check('Opening a feeling shows what brings it on', 'deadline' in det, det[:120])
        page.locator('.fcard').scroll_into_view_if_needed(); shot('06-trends')

        # cards come from patterns
        tap(".tab[data-tab='cards']")
        if page.locator("[data-act='make-cards']").count(): tap("[data-act='make-cards']")
        page.wait_for_timeout(300)
        body = page.inner_text('#view')
        check('Cards spot the recurring worry', 'deadline keeps coming up' in body.lower(), body[:900])
        check('Cards spot who lifts you', 'priya' in body.lower(), body[:300])
        check('Cards link to the days behind them', page.locator(".icard [data-act='open-entry']").count() >= 2)
        shot('07-cards')

        # deep dive
        tap(".tab[data-tab='dives']")
        tap(".dtile[data-id='stress']")
        if page.locator("[data-act='write-dive']").count(): tap("[data-act='write-dive']")
        page.wait_for_timeout(300)
        rep = page.inner_text('#d-body')
        check('Stress deep dive names the feelings', 'anxious' in rep.lower(), rep[:200])
        tap("[data-act='rep-tab'][data-k='act']")
        rep = page.inner_text('#d-body')
        check('Deep dive lists what weighs on you', 'deadline' in rep.lower(), rep[:300])
        shot('08-dive')
        page.keyboard.press('Escape')

        # dark mode look
        if SHOTS:
            page.emulate_media(color_scheme='dark'); tap(".tab[data-tab='diary']"); page.wait_for_timeout(300); shot('09-dark')

        # examples still work and have feelings
        check('No page errors', not errors, '; '.join(errors[:3]))
        browser.close()
    shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(ok for _, ok in results)
    print(f'\n{passed} of {len(results)} pass (feelings)')
    return passed == len(results)


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
