"""Synthetic browser proof. Build and start Vite preview, then run with --base-url http://127.0.0.1:4317."""
import argparse
import json
import io
import wave
from playwright.sync_api import sync_playwright, expect


def score(dense=False):
    def note(duration, tie=""):
        return f'<note><pitch><step>E</step><octave>1</octave></pitch><duration>{duration}</duration><voice>1</voice>' + (f'<notations><tied type="{tie}"/></notations>' if tie else '') + '</note>'
    notes = note(1) + note(1) + note(298) if dense else note(100) + '<note><rest/><duration>100</duration><voice>1</voice></note>' + note(100)
    return f'<score-partwise version="4.0"><part-list><score-part id="P"><part-name>Synthetic</part-name></score-part></part-list><part id="P"><measure number="1"><attributes><divisions>100</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>F</sign><line>4</line></clef></attributes><direction><sound tempo="60"/></direction>{notes}{note(100,"start")}</measure><measure number="2">{note(100,"stop")}<note><rest/><duration>300</duration><voice>1</voice></note></measure></part></score-partwise>'


def normal_dense_score():
    bars = []
    for bar in range(4):
        notes = []
        for i in range(64):
            pitch = 'E' if bar == 0 and i == 63 else ['E', 'F', 'G', 'A'][i % 4]
            tone = '<rest/>' if i == 2 else f'<pitch><step>{pitch}</step><octave>2</octave></pitch>'
            tie = '<tie type="start"/><notations><tied type="start"/></notations>' if bar == 0 and i == 63 else '<tie type="stop"/><notations><tied type="stop"/></notations>' if bar == 1 and i == 0 else ''
            notes.append(f'<note>{tone}<duration>60</duration><type>64th</type>{tie}</note>')
        attributes = '<attributes><divisions>960</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>F</sign><line>4</line></clef></attributes><direction><sound tempo="60"/></direction>' if bar == 0 else ''
        bars.append(f'<measure number="{bar+1}">{attributes}{"".join(notes)}</measure>')
    return '<score-partwise version="4.0"><part-list><score-part id="P"><part-name>Synthetic</part-name></score-part></part-list><part id="P">' + ''.join(bars) + '</part></score-partwise>'


TRIAL_ID = "12345678-1234-4123-8123-123456789012"
SUMMARY = {
    "trial_id": TRIAL_ID, "status": "completed", "source_kind": "local-midi",
    "original_filename": "synthetic.mid", "input_sha256": "a"*64, "input_bytes": 100,
    "input_note_count": 3, "source_duration_seconds": 8, "last_note_end_seconds": 5,
    "storage_clock": {"ppq":960,"tempo_bpm":60,"meter":"4/4","musical_tempo_and_meter_verified":False},
    "config":{"strings":4,"tuning_midi":[28,33,38,43],"max_fret":24},
    "gp5":{"requested":False,"status":"not_requested","mapped_count":None,"unmappable":[]},
    "warnings":[], "error":None,
    "artifacts":[{"key":"storage-clock-xml","kind":"xml","filename":"synthetic.musicxml","bytes":100,"sha256":"b"*64,"url":f"/api/v1/midi-trials/{TRIAL_ID}/files/storage-clock-xml"}]
}


def metrics(page):
    return page.locator('.midi-trial-score-frame').evaluate('e => ({width:e.clientWidth, scroll:e.scrollWidth, page:document.documentElement.scrollWidth, viewport:innerWidth, svg:e.querySelectorAll("svg").length, viewerOverflow:e.closest(".score-viewer").scrollWidth > e.closest(".score-viewer").clientWidth})')


def run(base_url):
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'])
        page = browser.new_page(viewport={'width': 768, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def api(route):
            if route.request.url.endswith('/api/v1/jobs'):
                route.fulfill(content_type='application/json', body=json.dumps({'job_id':TRIAL_ID}))
            elif '/api/v1/jobs/' in route.request.url:
                route.fulfill(content_type='application/json', body=json.dumps({'job_id':TRIAL_ID,'status':'SUCCESS','files':['synthetic.musicxml']}))
            elif '/api/v1/files/' in route.request.url:
                route.fulfill(content_type='application/xml', body=normal_dense_score())
            elif route.request.url.endswith('/files/storage-clock-xml'):
                route.fulfill(content_type='application/xml', body=normal_dense_score() if 'dense=64' in page.url else score('dense=1' in page.url))
            elif route.request.url.endswith(TRIAL_ID):
                route.fulfill(content_type='application/json', body=json.dumps(SUMMARY | ({'input_note_count':251,'source_duration_seconds':16,'last_note_end_seconds':16} if 'dense=64' in page.url else {})))
            else:
                route.fulfill(status=404, content_type='application/json', body='{}')
        page.route('**/api/**', api)
        for spacing in ['time', 'engraved']:
            page.goto(f'{base_url}/midi-trial/saved/{TRIAL_ID}?spacing={spacing}&view=tab')
            expect(page.get_by_role('button', name='再生', exact=True)).to_be_enabled(timeout=30000)
            for width in [320, 375, 768, 1024, 1440]:
                page.set_viewport_size({'width':width,'height':900})
                page.wait_for_timeout(250)
                value = metrics(page)
                assert page.locator('.midi-trial-score-frame').bounding_box()['width'] <= value['viewport'], value
                assert value['svg'] > 0, value
                assert not value['viewerOverflow'], value
                if spacing == 'time':
                    assert value['scroll'] <= value['width'] + 1, value
                results.append({'spacing':spacing, **value})
            page.get_by_role('button', name='再生', exact=True).click()
            page.wait_for_timeout(650)
            page.get_by_role('button', name='一時停止', exact=True).click()
            slider = page.get_by_role('slider')
            paused = float(slider.input_value())
            assert paused > 0
            page.wait_for_timeout(250)
            assert abs(float(slider.input_value())-paused) < 0.03
            page.set_viewport_size({'width':375,'height':900})
            page.wait_for_timeout(300)
            assert abs(float(slider.input_value())-paused) < 0.03
            cursor = page.get_by_test_id('score-time-cursor')
            expect(cursor).to_be_visible()
            cursor_before = cursor.evaluate('e=>e.style.transform')
            page.get_by_label('譜面の大きさ').select_option('1.2')
            page.wait_for_timeout(300)
            assert abs(float(slider.input_value())-paused) < 0.03
            expect(cursor).to_be_visible()
            assert cursor.evaluate('e=>e.style.transform') != cursor_before
            slider.fill('5.5')
            page.wait_for_timeout(250)
            assert abs(float(slider.input_value())-5.5) < 0.03
            assert cursor.get_attribute('data-bar') == '2'
            page.get_by_role('button', name='停止', exact=True).click()
            expect(slider).to_have_value('0')
            page.get_by_role('button', name='再生', exact=True).click()
            page.wait_for_timeout(200)
            page.get_by_role('button', name='停止', exact=True).click()
            assert not page.get_by_role('alert').count(), page.get_by_role('alert').all_text_contents()
            page.get_by_role('checkbox', name='五線譜＋TAB', exact=False).uncheck()
            expect(page.get_by_role('button', name='再生', exact=True)).to_be_enabled(timeout=30000)
            expect(slider).to_have_value('0')
            page.get_by_role('checkbox', name='五線譜＋TAB', exact=False).check()
            expect(page.get_by_role('button', name='再生', exact=True)).to_be_enabled(timeout=30000)
            expect(slider).to_have_value('0')
        page.goto(f'{base_url}/midi-trial/saved/{TRIAL_ID}?spacing=time&view=tab&dense=1')
        expect(page.get_by_role('button', name='再生', exact=True)).to_be_enabled(timeout=30000)
        value = metrics(page)
        assert value['scroll'] > value['width'], value
        assert not value['viewerOverflow'], value
        assert page.locator('.midi-trial-score-frame').bounding_box()['width'] <= value['viewport'], value
        expect(page.get_by_text('音符が密な小節は', exact=False)).to_be_visible()
        frame = page.locator('.midi-trial-score-frame')
        frame.focus()
        frame.press('ArrowRight')
        page.wait_for_timeout(200)
        assert frame.evaluate('e=>e.scrollLeft') > 0
        page.get_by_role('slider').fill('3.5')
        page.wait_for_timeout(300)
        cursor = page.get_by_test_id('score-time-cursor')
        bounds = frame.bounding_box()
        cursor_bounds = cursor.bounding_box()
        assert bounds['x'] <= cursor_bounds['x'] <= bounds['x']+bounds['width']
        assert not page.get_by_role('alert').count(), page.get_by_role('alert').all_text_contents()
        results.append({'spacing':'dense-time', **value})
        # A 64-slot bar also needs a readable fallback in ordinary engraving.
        page.goto(f'{base_url}/midi-trial/saved/{TRIAL_ID}?spacing=engraved&view=tab&dense=64')
        expect(page.get_by_role('button', name='再生', exact=True)).to_be_enabled(timeout=30000)
        expect(page.get_by_text('音符が密な小節は', exact=False)).to_be_visible()
        value = metrics(page)
        assert value['scroll'] > value['width'], value
        assert not value['viewerOverflow'], value
        frame = page.locator('.midi-trial-score-frame')
        frame.focus()
        frame.press('ArrowRight')
        page.wait_for_timeout(200)
        assert frame.evaluate('e=>e.scrollLeft') > 0
        slider = page.get_by_role('slider')
        slider.fill('5.5')
        page.wait_for_timeout(300)
        cursor = page.get_by_test_id('score-time-cursor')
        expect(cursor).to_be_visible()
        assert cursor.get_attribute('data-bar') == '2'
        bounds = frame.bounding_box()
        cursor_bounds = cursor.bounding_box()
        assert bounds['x'] <= cursor_bounds['x'] <= bounds['x']+bounds['width']
        page.get_by_role('button', name='再生', exact=True).click()
        page.wait_for_timeout(250)
        page.get_by_role('button', name='一時停止', exact=True).click()
        paused = float(slider.input_value())
        page.set_viewport_size({'width':320,'height':900})
        page.wait_for_timeout(300)
        assert abs(float(slider.input_value())-paused) < 0.03
        assert not page.get_by_role('alert').count(), page.get_by_role('alert').all_text_contents()
        results.append({'spacing':'dense-engraved', **value})
        # Both older entry points use AlphaTabViewer and the same normal adapter.
        page.goto(f'{base_url}/basic-pitch')
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\x00\x00' * 800)
        page.get_by_label('Upload audio').set_input_files({'name':'synthetic.wav','mimeType':'audio/wav','buffer':audio.getvalue()})
        page.get_by_role('button', name='Create job', exact=True).click()
        for route_name in ['basic-pitch', 'demo']:
            if route_name == 'demo':
                page.goto(f'{base_url}/demo/{TRIAL_ID}')
            play = page.get_by_role('button', name='▶ Play', exact=True)
            expect(play).to_be_enabled(timeout=30000)
            expect(page.get_by_text('音符が密な小節は', exact=False)).to_be_visible()
            value = page.locator('.legacy-score-frame').evaluate('e=>({width:e.clientWidth,scroll:e.scrollWidth,viewport:innerWidth,viewerOverflow:e.closest(".score-viewer").scrollWidth>e.closest(".score-viewer").clientWidth})')
            assert value['scroll'] > value['width'], value
            assert not value['viewerOverflow'], value
            actual = page.evaluate("""() => {
              const host=document.getElementById('root');
              const key=Object.keys(host).find(k=>k.startsWith('__reactContainer'));
              const stack=[host[key].stateNode.current];let api;
              while(stack.length){const fiber=stack.pop();if(fiber.child)stack.push(fiber.child);if(fiber.sibling)stack.push(fiber.sibling);
                let hook=fiber.memoizedState;while(hook&&typeof hook==='object'){const ref=hook.memoizedState?.current;if(ref?.renderer&&ref?.tickCache&&ref?.score)api=ref;hook=hook.next;}}
              if(!api)throw Error('Cannot inspect actual legacy renderer');
              window.__syntheticLegacyApi=api;
              const bounds=api.renderer.boundsLookup.findMasterBar(api.score.masterBars[0]);
              const columns=bounds.bars[0].beats.map(b=>b.onNotesX);
              return {minGap:Math.min(...columns.slice(1).map((x,i)=>x-columns[i])),workers:api.settings.core.useWorkers};
            }""")
            assert actual['minGap'] >= 12, actual
            assert actual['workers'] is False
            play.click()
            page.wait_for_timeout(350)
            assert page.evaluate('window.__syntheticLegacyApi.timePosition') > 0
            page.get_by_role('button', name='⏸ Pause', exact=True).click()
            page.wait_for_timeout(100)
            paused = page.evaluate('window.__syntheticLegacyApi.timePosition')
            page.wait_for_timeout(250)
            assert abs(page.evaluate('window.__syntheticLegacyApi.timePosition')-paused) < 30
            page.get_by_role('button', name='⏹ Stop', exact=True).click()
            results.append({'spacing':f'dense-legacy-{route_name}', **value, **actual})
        assert not errors, errors
        browser.close()
    print(json.dumps({'checks':results, 'page_errors':errors, 'outside_score_page_overflow': any(item['page'] > item['viewport'] for item in results)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:4317')
    run(parser.parse_args().base_url)
