# Nextcloud SFTP Uploader

A desktop GUI tool for uploading large files and folders directly to a Nextcloud server via SSH/SFTP — bypassing WebDAV limitations, chunking timeouts, and Cloudflare restrictions.

Built for home lab setups where the official Nextcloud desktop client struggles with very large files (10 GB+).

---

## Why this exists

The official Nextcloud desktop client uses WebDAV chunked uploads. When a file is fully uploaded, the server must **assemble all chunks into one file** (the MOVE phase). For very large files this can take several minutes — and if the connection times out during assembly (e.g. due to Cloudflare Tunnel), the entire upload is restarted from scratch.

This tool avoids that problem entirely by sending files **directly over SSH/SFTP** into a temp directory on the same filesystem as Nextcloud data. The final move is an **atomic kernel rename** (`mv`) — instantaneous regardless of file size — followed by a `files:scan` to register the file in Nextcloud's database.

---

## Features

- Upload files and folders of any size (tested with 120 GB+)
- Progress bar with real-time speed and ETA
- Automatic `files:scan` after upload — files appear in Nextcloud immediately
- Fresh SFTP session per file — fixes `Bad message` / `Socket closed` errors on multi-file folders
- `mv` is instant because `/tmp` and Nextcloud data are on the same filesystem
- Clean tmp button — removes leftover temp files if upload was interrupted
- Remember credentials option (SSH credentials saved, sudo password never saved)
- Cancel button works at any stage

---

## Download

Go to the Releases page and download the latest `ncsender.exe`.

No installation required — just run the exe.

> **Note:** Windows Defender or other antivirus software may flag the exe as suspicious because it is not code-signed. This is a false positive. 

---

## Requirements

### Client (Windows)
- Windows 10 or newer
- No Python or additional software needed

### Server
- Linux with SSH access
- Nextcloud installed (tested with Nextcloud 29–33)
- User with `sudo` access
- `/tmp` on the same filesystem as Nextcloud data directory (standard on most setups)

---

## Configuration

Fill in the fields in the GUI:

- **Host** — SSH server IP or hostname, e.g. `192.168.1.100`
- **Port** — SSH port, default `22`
- **SSH User** — your Linux username
- **SSH Password** — SSH login password
- **sudo Password** — sudo password, never saved to disk
- **NC User** — your Nextcloud username
- **data/ path** — path to Nextcloud data directory, e.g. `/var/www/html/nextcloud/data`
- **occ path** — path to Nextcloud occ script, e.g. `/var/www/html/nextcloud/occ`
- **Target folder** — destination folder inside Nextcloud, e.g. `/Downloads`

Check **Remember login credentials** to save SSH credentials between sessions. The sudo password is **never** written to disk.

---

## How it works

```
Windows                          Linux Server
───────                          ────────────
Select file/folder
        │
        ▼
SSH connect ─────────────────► Create /tmp/.nc_upload_<random>/
        │
        ▼
SFTP upload ─────────────────► /tmp/.nc_upload_<random>/<file>
(fresh session per file)
        │
        ▼
sudo script ─────────────────► mv → /nextcloud/data/user/files/SHARE/<file>
                                chown www-data:www-data
                                chmod 640
                                occ files:scan
        │
        ▼
✓ Done — file visible in Nextcloud
```

---

## Comparison with official Nextcloud client

**Protocol**
- Official client: WebDAV (chunked upload)
- This tool: SSH/SFTP

**Works through Cloudflare Tunnel**
- Official client: ✗ — times out during chunk assembly (MOVE phase)
- This tool: ✓ — each SFTP transfer is a direct TCP connection, no HTTP timeouts

**File size limit**
- Official client: ~10–20 GB in practice
- This tool: Unlimited (tested with 120 GB+)

**Requires SSH access to server**
- Official client: ✗
- This tool: ✓ — needs SSH login and sudo

**Auto-sync**
- Official client: ✓ — monitors folders and syncs automatically
- This tool: ✗ — manual upload only

**Speed on LAN**
- Both: ~90 MB/s (limited by network, not the tool)

---

---

## Troubleshooting

**`Bad message` or `Socket closed` on folder upload**
Already handled — the tool opens a fresh SFTP session for each file.

**`files:scan` fails**
Check that the `occ path` field points to the correct location. Default is `/var/www/html/nextcloud/occ`.

**Upload succeeds but file not visible in Nextcloud**
Run `files:scan` manually on the server:
```bash
sudo -u www-data php /var/www/html/nextcloud/occ files:scan <username> --quiet
```

---

## Security notes

- SSH credentials are saved to `%APPDATA%\nextcloud_sftp_uploader.json` if "Remember" is checked
- **sudo password is never saved** — must be entered each session
- Temp files are created in `/tmp` with `chmod 700` (owner-only access)
- Temp directory name includes 16 random hex characters

---

## License

MIT License — do whatever you want with it.

---

## Author

Made by [nyorbee](https://www.facebook.com/ajtiguy)
