# HopDrop

Send very large files (10 GB, 12 GB, more) between your own devices. Two ways to use it:

## 1. Website (`index.html`): device to device, from anywhere

A static page that sends files **directly** from one browser to another over WebRTC. Nothing is uploaded to a server, so there is no size limit and nothing to pay for.

1. On the sending device (e.g. your phone) open the site and choose your files.
2. Open the link (or scan the QR code / type the 6-letter code) on the receiving device.
3. On the receiver, click **Download** on a file and pick where to save it.

Notes
- Both devices must stay online with the page open until the transfer ends. If the connection drops, click Download again (no resume yet).
- For files over ~1.5 GB, receive with **Chrome or Edge on a computer**. They stream straight to disk. Other browsers can't write huge files without running out of memory.
- Sending works from any modern phone or computer browser.
- Some strict networks (certain mobile carriers, corporate Wi-Fi) block direct connections. If two devices can't connect, try the same Wi-Fi or use the local script below.
- The free PeerJS cloud server is used only to introduce the two devices to each other. File data never goes through it.

### Hosting it (free, GitHub Pages)
Repo **Settings → Pages → Build and deployment → Deploy from a branch → `main` / `(root)` → Save**.
The site will be at `https://djsergio88.github.io/HOPDROP_by_Serg/`.

## 2. Local script (`hopdrop.py`): phone to PC on the same Wi-Fi

Fast, resumable, PIN-protected receiver for your PC. Needs only Python 3.

```
python hopdrop.py                 # saves to Downloads\HopDrop
python hopdrop.py --max-gb 20 --dir D:\Transfers --port 9000
```

Open the printed address on your phone, enter the PIN, pick files. Interrupted uploads continue where they stopped.
