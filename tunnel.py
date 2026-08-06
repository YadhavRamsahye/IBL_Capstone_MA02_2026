"""
tunnel.py — expose the local server to mobile via a public ngrok URL.
Run this in a second terminal while main.py is running:
    python tunnel.py
"""
from pyngrok import ngrok, conf
import time, sys

PORT = 8000

print("=" * 55)
print("  Traffic App — Mobile Tunnel")
print("=" * 55)

try:
    tunnel = ngrok.connect(PORT, "http")
    url = tunnel.public_url
    # pyngrok returns http:// — also print the https variant
    https_url = url.replace("http://", "https://")

    print(f"\n  Public URL (use this on your phone):")
    print(f"\n      {https_url}\n")
    print(f"  Also works: {url}")
    print("\n  Share this link with anyone — works over mobile data too.")
    print("  Press Ctrl+C to stop the tunnel.\n")
    print("=" * 55)

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\nClosing tunnel…")
    ngrok.kill()
    sys.exit(0)
except Exception as e:
    print(f"\n[Error] {e}")
    print("\nIf you see an auth error, get a free token at https://dashboard.ngrok.com")
    print("Then run:  python -m pyngrok authtoken YOUR_TOKEN_HERE")
    sys.exit(1)
