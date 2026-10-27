"""Opt-in Chromium checks against an isolated local Monitor test instance.

Requires an already installed browser/Playwright, never downloads either.
MONITOR_BROWSER_URL, MONITOR_BROWSER_DATA and MONITOR_BROWSER_ARTIFACTS must
explicitly point at the test instance and its disposable data/artifact paths.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest


def test_inventory_desktop_mobile_and_history():
    pw = pytest.importorskip('playwright.sync_api')
    url = os.environ.get('MONITOR_BROWSER_URL','')
    data = os.environ.get('MONITOR_BROWSER_DATA','')
    artifacts = os.environ.get('MONITOR_BROWSER_ARTIFACTS','')
    if not url or not data or not artifacts:
        pytest.skip('Explicit isolated browser test instance not configured')
    parsed = urlsplit(url)
    assert parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.port
    folder=Path(data).resolve()
    assert folder not in (Path('/var/lib/monitor'),Path('/opt/monitor'))
    output=Path(artifacts);output.mkdir(parents=True,exist_ok=True)
    password=next(line.split(': ',1)[1] for line in (folder/'admin-credentials.txt').read_text().splitlines() if line.startswith('Mot de passe'))
    with pw.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url)
        page.get_by_label('Mot de passe',exact=True).fill(password)
        page.get_by_role('button',name='Se connecter').click()
        pw.expect(page.locator('#shell')).to_be_visible(timeout=15000)
        page.get_by_role('link',name='Inventaire',exact=True).click()
        pw.expect(page.locator('#page-title')).to_have_text('Inventaire')
        pw.expect(page.locator('[data-collector="docker_topology"]')).to_be_visible(timeout=15000)
        page.locator('[data-collector="docker_topology"] summary').first.click()
        pw.expect(page.locator('[data-collector="docker_topology"]')).to_contain_text('observée')
        page.get_by_label('Rechercher dans l’inventaire').fill('Pression')
        pw.expect(page.locator('.inventory-card')).to_have_count(1)
        pw.expect(page.locator('.inventory-card')).to_contain_text('Pression des ressources')
        page.locator('.inventory-card button').click()
        pw.expect(page.locator('#dialog')).to_be_visible()
        pw.expect(page.locator('#dialog-body')).to_contain_text('24 heures')
        page.get_by_role('button',name='Fermer',exact=True).click()
        page.get_by_label('Rechercher dans l’inventaire').fill('')
        pw.expect(page.locator('.inventory-card')).to_have_count(12)
        page.locator('[data-collector="containers"] button').click()
        pw.expect(page.locator('#dialog-body')).to_contain_text('Inventaires enregistrés')
        # A reused isolated database has actual pre/post collection snapshots.
        pw.expect(page.locator('#inventory-compare')).to_be_visible()
        page.locator('#inventory-compare button').click()
        pw.expect(page.locator('#inventory-comparison')).to_contain_text('Modifications')
        page.get_by_role('button',name='Fermer',exact=True).click()
        with page.expect_download() as download:
            page.get_by_role('link',name='Export JSON',exact=True).click()
        path=download.value.path()
        exported=json.loads(Path(path).read_text())
        assert len(exported['collectors'])==12
        page.screenshot(path=str(output/'inventory-desktop-final.png'),full_page=True)
        page.get_by_role('link',name='Logiciels',exact=True).click()
        pw.expect(page.locator('#content')).to_contain_text('Paquets système')
        page.get_by_role('link',name='Inventaire',exact=True).click()
        page.set_viewport_size({'width':390,'height':844})
        # Wait for the responsive navigation transition before capturing evidence.
        pw.expect(page.locator('.sidebar')).not_to_be_in_viewport()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        page.screenshot(path=str(output/'inventory-mobile-final.png'),full_page=True)
        page.get_by_role('button',name='Ouvrir la navigation',exact=True).click()
        pw.expect(page.locator('.sidebar')).to_be_in_viewport()
        page.get_by_role('link',name='Système',exact=True).click()
        pw.expect(page.locator('#page-title')).to_have_text('Système')
        assert not errors,errors
        browser.close()
