# HopDrop

Send big files (up to 12 GB per file by default) to anyone, on any device or network. Upload, share the link, the file is deleted after 7 days.

- `public/index.html`: upload page (resumable, 32 MB parts, 3 in parallel)
- `public/download.html`: what people see when they open a link (`/d/<id>`)
- `public/direct.html`: optional device-to-device mode (WebRTC, nothing stored)
- `src/worker.js`: Cloudflare Worker with the API; files are stored in Cloudflare R2
- `hopdrop.py`: local script for phone-to-PC on the same Wi-Fi (no internet needed)
- `wrangler.toml`: Cloudflare configuration

## Deploy (one time)

1. **Cloudflare account** (free): https://dash.cloudflare.com. Go to **R2 Object Storage** and enable it (a payment card is required, the free tier covers the first 10 GB).
2. **Create the bucket** named `hopdrop-files`.
3. **7-day expiry**: open the bucket, **Settings → Object lifecycle rules → Add rule**, delete objects after **7 days**. (The Worker also refuses expired files and deletes them on access.)
4. **Upload this project to GitHub** (replace the old files in your repo).
5. **Connect GitHub to Cloudflare**: **Workers & Pages → Create → Import a repository** → pick `HOPDROP_by_Serg` → deploy. Every push to `main` then redeploys automatically. The site is live at `https://hopdrop.<your-account>.workers.dev`.
6. **Your own address**: register `hopdrop.io` (or any domain), add it to Cloudflare, then in the Worker open **Settings → Domains & Routes → Add → Custom domain**.

Change the size limit in `wrangler.toml` (`MAX_BYTES`).

## Running a public upload site: things to know

- Anyone can upload, so you pay for the storage and you are responsible for what is hosted. Add Cloudflare **Rate limiting rules** or **Turnstile** if abuse appears, and keep an address where people can report content.
- Downloads are always served as attachments (never rendered in the browser) to avoid hosting phishing pages.

## Local script

```
python hopdrop.py                 # saves to Downloads\HopDrop
python hopdrop.py --max-gb 20 --dir D:\Transfers --port 9000
```
