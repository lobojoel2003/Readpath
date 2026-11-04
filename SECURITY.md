# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/lobojoel2003/Readpath/security/advisories/new)
rather than in a public issue. Include steps to reproduce and, if possible, a
sample file. You should get a reply within a week.

## Running Readpath safely

Readpath opens PDFs from the internet and fetches web pages on request, so:

- **Set `READPATH_PASSWORD`** if the server is reachable from the internet.
  Without it, anyone who can reach the server can start scans and download files.
- **Keep `READPATH_ALLOW_PRIVATE_HOSTS` off** (the default). It stops the crawler
  from being used to reach addresses on your internal network.
- **Run the Docker image as provided.** It runs as an unprivileged user and keeps
  data in the `/data` volume.
- Put Readpath behind HTTPS (a reverse proxy such as Caddy or nginx) when it is
  used over a network.
- Uploaded and downloaded files are stored unencrypted in the data volume.
  Don't use Readpath for confidential documents unless the host is protected
  accordingly.
