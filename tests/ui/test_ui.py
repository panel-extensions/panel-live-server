"""UI Test Module."""
# import time

import pytest

pytest.importorskip("playwright")

# from panel.pane import panel
# from panel.tests.util import serve_component
# from playwright.sync_api import expect

pytestmark = pytest.mark.ui


def test_param_defer_load(page):
    """Example of a UI test using Playwright."""
    # def defer_load():
    #     time.sleep(0.5)
    #     return "I render after load!"

    # component = panel(defer_load, defer_load=True)

    # serve_component(page, component)

    # assert page.locator(".pn-loading")
    # expect(page.locator(".markdown").locator("div")).to_have_text("I render after load!\n")


def test_auto_eda_report_tabs_mount(tmp_path):
    """A rendered auto_eda report mounts its tabs in a real (Chromium) browser."""
    import socket

    import pandas as pd
    from playwright.sync_api import sync_playwright

    from panel_live_server.client import DisplayClient
    from panel_live_server.manager import PanelServerManager

    csv = tmp_path / "ui.csv"
    pd.DataFrame({"a": [1.0, 2, 3, 4, 5, 6], "b": ["x", "y", "x", "y", "x", "y"]}).to_csv(csv, index=False)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    manager = PanelServerManager(db_path=str(tmp_path / "db.sqlite"), port=port, host="localhost", max_restarts=1)
    assert manager.start()
    try:
        client = DisplayClient(base_url=manager.get_base_url())
        code = (
            "import panel as pn\n"
            'pn.extension("tabulator")\n'
            "from panel_live_server import eda\n"
            f"df = eda.load_source({str(csv)!r})\n"
            'eda.build_report(df, title="UI Smoke").servable()\n'
        )
        resp = client.create_snippet(code=code, name="UI Smoke", method="server", validated=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(resp["url"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector("text=Overview", timeout=45000)
            assert page.get_by_text("Summary").count() >= 1
            browser.close()
    finally:
        manager.stop()
