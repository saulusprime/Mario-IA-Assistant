"""Diagnostica di connessione: mostra come BlueZ vede Mario e prova a connettersi.
Uso (sull'host): .venv/bin/python tools/diag_connect.py
"""
import asyncio
import subprocess
import time

from bleak import BleakClient, BleakScanner

LEGO = 0x0397
KEYS = ["Address", "AddressType", "Name", "Alias", "Paired", "Bonded", "Trusted", "Blocked", "LegacyPairing",
        "Connected", "ServicesResolved", "AdvertisingFlags", "UUIDs", "Class", "Icon", "Appearance", "RSSI", "TxPower"]


def is_mario(adv):
    data = adv.manufacturer_data.get(LEGO)
    return data is not None and len(data) > 1 and data[1] in (0x43, 0x44, 0x45)


def show(dev):
    props = (dev.details or {}).get("props", {})
    for key in KEYS:
        if key in props:
            value = props[key]
            if isinstance(value, (bytes, bytearray)):
                value = bytes(value).hex(" ")
            print(f"   {key}: {value}")


async def find(holder, timeout):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def cb(d, a):
        if not fut.done() and is_mario(a):
            fut.set_result(d)

    scanner = BleakScanner(cb)
    await scanner.start()
    holder.append(scanner)
    return await asyncio.wait_for(fut, timeout)


async def try_connect(dev):
    client = BleakClient(dev, timeout=20)
    try:
        await client.connect()
        print("   CONNESSO. Servizi:", [s.uuid for s in client.services])
        await asyncio.sleep(1)
        await client.disconnect()
        return True
    except Exception as exc:
        print(f"   connect fallita: {type(exc).__name__}: {exc}")
        return False


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


async def main():
    holder = []
    t0 = time.time()
    print("1) attendo Mario (120 s)...")
    dev = await find(holder, 120)
    print(f"   trovato {dev.address} dopo {time.time() - t0:.0f} s. Proprietà BlueZ:")
    show(dev)
    ok = await try_connect(dev)
    for s in holder:
        await s.stop()
    if ok:
        print("RISULTATO: ok al primo tentativo")
        return
    print("2) bluetoothctl info:")
    print("   " + sh("bluetoothctl", "info", dev.address).replace("\n", "\n   ")[:700])
    print("3) rimuovo il record da BlueZ e riprovo:", sh("bluetoothctl", "remove", dev.address))
    holder = []
    dev2 = await find(holder, 30)
    print(f"   ritrovato {dev2.address}. Proprietà BlueZ:")
    show(dev2)
    ok = await try_connect(dev2)
    for s in holder:
        await s.stop()
    print("RISULTATO:", "ok dopo la rimozione del record" if ok else "fallito anche dopo la rimozione")


asyncio.run(main())
