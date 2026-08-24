from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp import __version__


class TestSynoraApp(FrappeTestCase):
    def test_app_version(self) -> None:
        self.assertEqual(__version__, "0.0.1")
