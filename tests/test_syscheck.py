"""Controllo del PC (`./run.sh doctor`) su un sysfs finto con la stessa struttura del MacBook (adattatore Apple
05ac:8290 sull'interfaccia 1-3:1.2, rfkill, Wi-Fi brcmfmac) e comandi simulati."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from mario import syscheck

LEGO = "00001623-1212-efde-1623-785feabcd123"


def fake_sys(root: Path, control="on", param="Y", soft="0", wifi_up=True, vendor="05ac"):
    usb = root / "devices" / "usb1" / "1-3"
    iface = usb / "1-3:1.2"
    (usb / "power").mkdir(parents=True)
    iface.mkdir()
    for name, value in (("idVendor", vendor), ("idProduct", "8290")):
        (usb / name).write_text(value + "\n")
    (usb / "power" / "control").write_text(control + "\n")
    for name, value in (("Class", "ff"), ("SubClass", "01"), ("Protocol", "01")):
        (iface / f"bInterface{name}").write_text(value + "\n")
    drivers = root / "bus" / "drivers"
    for d in ("btusb", "brcmfmac"):
        (drivers / d).mkdir(parents=True)
    (iface / "driver").symlink_to(drivers / "btusb")
    hci = root / "class" / "bluetooth" / "hci0"
    hci.mkdir(parents=True)
    (hci / "device").symlink_to(iface)
    (root / "class" / "bluetooth" / "hci0:3585").mkdir()  # una connessione, da ignorare
    rf = root / "class" / "rfkill" / "rfkill0"
    rf.mkdir(parents=True)
    for name, value in (("type", "bluetooth"), ("name", "hci0"), ("soft", soft), ("hard", "0")):
        (rf / name).write_text(value + "\n")
    params = root / "module" / "btusb" / "parameters"
    params.mkdir(parents=True)
    (params / "enable_autosuspend").write_text(param + "\n")
    net = root / "class" / "net" / "wlp3s0"
    (net / "wireless").mkdir(parents=True)
    (net / "device").mkdir()
    (net / "device" / "driver").symlink_to(drivers / "brcmfmac")
    (net / "operstate").write_text("up\n" if wifi_up else "down\n")
    (root / "class" / "net" / "lo").mkdir()


def runner(outputs):
    def run(cmd):
        return outputs.get(" ".join(cmd))
    return run


GOOD = {
    "systemctl is-active bluetooth": "active\n",
    "bluetoothctl show": "Controller 00:11:22:33:44:55 (public)\n\tPowered: yes\n",
    "nmcli -t -f IN-USE,FREQ dev wifi list ifname wlp3s0 --rescan no": " :2437 MHz\n*:5200 MHz\n",
    "bluetoothctl devices Paired": "Device AA:BB:CC:DD:EE:01 Mario\nDevice AA:BB:CC:DD:EE:02 Cuffie\n",
    "bluetoothctl info AA:BB:CC:DD:EE:01": f"\tPaired: yes\n\tUUID: Vendor specific ({LEGO})\n",
    "bluetoothctl info AA:BB:CC:DD:EE:02": "\tPaired: yes\n\tUUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)\n",
    "bluetoothctl --version": "bluetoothctl: 5.82\n",
}


class SysCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sys"
        self.etc = Path(self.tmp.name) / "modprobe.d"
        self.etc.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def checks(self, outputs=GOOD, **kw):
        fake_sys(self.root, **kw)
        return syscheck.run_checks(self.root, runner(outputs), dirs=(self.etc,))

    def by_name(self, checks, name):
        return [c for c in checks if c.name == name]

    def test_healthy_macbook(self):
        (self.etc / "btusb.conf").write_text("# niente autosospensione\noptions btusb enable_autosuspend=0\n")
        checks = self.checks()
        self.assertEqual(syscheck.worst(checks), syscheck.OK)
        adapter = self.by_name(checks, "adattatore")
        self.assertEqual(len(adapter), 1)  # hci0:3585 è una connessione, non un adattatore
        self.assertIn("05ac:8290 (Apple, chip Broadcom), driver btusb", adapter[0].detail)
        self.assertIn("nessun file .hcd", self.by_name(checks, "firmware")[0].detail)
        auto = self.by_name(checks, "autosospensione")[0]
        self.assertIn("dal prossimo avvio vale", auto.detail)
        self.assertIn("5 GHz (5200 MHz)", self.by_name(checks, "Wi-Fi")[0].detail)
        self.assertEqual(self.by_name(checks, "pairing")[0].detail, "hub LEGO associati (bond): AA:BB:CC:DD:EE:01")
        self.assertIn("BlueZ 5.82", self.by_name(checks, "versioni")[0].detail)
        self.assertNotIn("→", syscheck.format_checks(checks))
        self.assertEqual(json.loads(json.dumps(syscheck.as_dicts(checks)))[0]["status"], "ok")

    def test_known_problems_and_fixes(self):
        bad = dict(GOOD, **{"systemctl is-active bluetooth": "inactive\n",
                            "nmcli -t -f IN-USE,FREQ dev wifi list ifname wlp3s0 --rescan no": "*:2412 MHz\n",
                            "bluetoothctl devices Paired": ""})
        checks = self.checks(bad, control="auto", soft="1")
        self.assertEqual(syscheck.worst(checks), syscheck.ERROR)
        auto = self.by_name(checks, "autosospensione")[0]
        self.assertEqual(auto.status, syscheck.WARN)
        self.assertIn(str(self.root / "devices/usb1/1-3/power/control"), auto.fix)
        self.assertIn("/etc/modprobe.d/btusb.conf", auto.fix)  # manca anche la regola persistente
        wifi = self.by_name(checks, "Wi-Fi")[0]
        self.assertEqual((wifi.status, "2,4 GHz (2412 MHz)" in wifi.detail), (syscheck.WARN, True))
        self.assertIn("5 GHz", wifi.fix)
        self.assertEqual(self.by_name(checks, "rfkill")[0].fix, "rfkill unblock bluetooth")
        self.assertEqual(self.by_name(checks, "servizio")[0].fix, "sudo systemctl start bluetooth")
        self.assertIn("nessun hub LEGO", self.by_name(checks, "pairing")[0].detail)
        text = syscheck.format_checks(checks)
        self.assertIn("→ rfkill unblock bluetooth", text)
        self.assertIn("Bluetooth non utilizzabile", text)

    def test_variants(self):
        outputs = dict(GOOD)
        del outputs["nmcli -t -f IN-USE,FREQ dev wifi list ifname wlp3s0 --rescan no"]
        outputs["iw dev wlp3s0 link"] = "Connected to xx\n\tfreq: 5180.0\n"  # senza nmcli si usa iw
        checks = self.checks(outputs, param="N", vendor="0b05")  # chiavetta non Apple, opzione già attiva
        self.assertIn("5 GHz (5180 MHz)", self.by_name(checks, "Wi-Fi")[0].detail)
        self.assertIn("opzione del modulo", self.by_name(checks, "autosospensione")[0].detail)
        self.assertEqual(self.by_name(checks, "firmware"), [])
        manual = syscheck.check_autosuspend(syscheck.adapters(self.root), self.root, dirs=(self.etc,))
        self.assertEqual(manual[0].status, syscheck.OK)
        (self.root / "module/btusb/parameters/enable_autosuspend").write_text("Y\n")
        manual = syscheck.check_autosuspend(syscheck.adapters(self.root), self.root, dirs=(self.etc,))
        self.assertEqual((manual[0].status, "riattiva" in manual[0].detail), (syscheck.WARN, True))  # niente .conf
        self.assertEqual(syscheck.check_adapters([])[0].status, syscheck.ERROR)
        self.assertEqual(syscheck.check_powered(runner({}))[0].status, syscheck.INFO)  # bluetoothctl assente
        self.assertEqual(syscheck.check_powered(runner({"bluetoothctl show": "\tPowered: no\n"}))[0].fix,
                         "bluetoothctl power on")

    def test_flatpak_sandbox_is_partial(self):
        fake_sys(self.root)
        marker = Path(self.tmp.name) / "flatpak-info"
        marker.write_text("[Application]\n")
        old = syscheck.FLATPAK_INFO, syscheck.SYS
        syscheck.FLATPAK_INFO, syscheck.SYS = marker, self.root
        try:
            checks = syscheck.run_checks(self.root, runner(GOOD), dirs=(self.etc,))
        finally:
            syscheck.FLATPAK_INFO, syscheck.SYS = old
        self.assertEqual(checks[0].name, "sandbox")
        self.assertEqual(checks[0].fix, "./run.sh doctor (esegue il controllo sull'host)")
        self.assertEqual(self.by_name(checks, "autosospensione"), [])  # /etc del sandbox: niente falsi allarmi

    @unittest.skipUnless(importlib.util.find_spec("bleak"), "serve bleak (venv dell'host)")
    def test_cli_command(self):
        from mario import cli
        args = cli.build_parser().parse_args(["doctor", "--json"])
        self.assertIs(args.func, cli.cmd_doctor)
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
