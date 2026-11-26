# Recon: what a site says about itself

The `recon` tool (item 776) is where a security engineer starts, without a browser and without touching the site: it
reads the addresses a name has, the TLS certificate it gives, the HTTP answer and its headers, and the technology its
headers and its page name. From those it writes the findings a site's owner should fix. With `depth = "ports"` it also
asks which of the common ports answer, and reads the banner a service gives before anything is sent to it.

It only reads. It sends no payload, guesses no password, signs in nowhere and tries no exploit: a finding names what is
wrong, and what to do about it is the run's work or yours. It also does not keep a vulnerability database: a service that
names its version is a finding, and whether that version has a known bug is a question for the vendor's advisories.

## The two depths

**`passive`** (the default) reads what a browser's visit reads, on any public site:

- the A and AAAA addresses of the name;
- the certificate on port 443, or on the target's own port when it names one: for whom it was issued, by whom, until
  when, which names it covers, and whether it verifies on this Mac;
- one GET, following redirects, with the status, the media type, the title and the headers;
- the stack the headers and the page name (`Server`, `X-Powered-By`, a generator meta tag, the paths of a known CMS or
  framework, Cloudflare, Vercel, AWS, Shopify);
- findings: a missing `Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options` (or one that is
  not `nosniff`), `X-Frame-Options` or a `frame-ancestors` policy, `Referrer-Policy` and `Permissions-Policy`; a
  `Server` or `X-Powered-By` header that names a version; `Access-Control-Allow-Origin: *`; a certificate that expires
  within 30 days, has expired, does not verify or does not name the host.

**`ports`** adds a TCP connect to the 34 usual public services (ftp, ssh, smtp, http, https, smb, imap, mysql,
postgresql, redis, mongodb, rdp, vnc, elasticsearch and the rest) and reads the banner of the ones that greet first.
It runs **only on a host you own or a scope allows**: scanning a host is not reading a page, and the one who runs it
must be the one who may.

```toml
[recon]
# Your own sites; a name covers its subdomains. An empty list allows no scan anywhere.
owned_hosts = ["example.com", "senoldogan.dev"]
```

## A bug bounty program's scope: one command

A target that is someone else's is tested under a scope: the published list of a program or an engagement you are in.
`dotx scope` keeps those, writes them, and answers who covers a host — you never edit a toml block for it (item 1888).

```
dotx scope add --program "Bugcrowd · Klaviyo" \
    --url https://bugcrowd.com/engagements/klaviyo-og \
    --hosts a.klaviyo.com,klaviyo.com,myklpages.com \
    --note "no scanners; 5 req/s; other subdomains out of scope"

dotx scope check https://api.klaviyo.com/v1/lists     # -> may be tested under Bugcrowd · Klaviyo
dotx scope list                                        # every scope, its hosts and its rules
dotx scope remove "Bugcrowd · Klaviyo"
```

The scopes live in `scopes.toml` in Dot's home, which `dotx scope` writes and every call reads by its stamp: a scope
added in one run holds for the next one without a restart. A file that cannot be read (not TOML, a host with a
wildcard) is named with what is wrong: the port scan and `security_scan` wait for it, the passive audit goes on and its
report says the scope is unknown. A run whose commands run outside the sandbox (a yolo run) registers the program
itself — it reads the program's published scope in the browser, then one `dotx scope add` — and goes on. Under the
sandbox Dot's home is closed to a run's commands ([Safety](safety.md)), so there the run hands you that one
`dotx scope add` line to run, never a toml block.

A host in a scope is treated as an owned host: the port scan and `security_scan` run on it, and every report ends with
`Scope: <program> — <rules>`. A scope stands for your word that you are in the program and that its rules allow the
test: you write it, or a run you asked to test that program writes it from the program's own scope page, and `dotx
scope list` shows what each holds, so remove one you did not mean. The `note` is where its rate limit, its exclusions
and what it bans belong. When a model declines the work, those rules are quoted to it as recorded, never as Dot's own
words ([Models](models.md), item 1889). A scope may also be written in the config file
as `[[recon.scopes]]`; the store is the place `dotx scope` keeps, and both are read.

A port whose service should stay behind the firewall (a database, SMB, RDP, VNC, Redis, memcached, Elasticsearch) is a
finding of its own; so is a banner that names a version. An entry is a bare host name: a wildcard, a port or a scheme
is refused, and a name covers its subdomains.

## How a run calls it

```
recon(target="example.com")                  # the public face
recon(target="https://example.com/shop")     # the same, with the path read
recon(target="example.com", depth="ports")   # the ports too, only when example.com is in owned_hosts or a scope
```

The report is written for a person: the addresses, the HTTP answer, the certificate, the technology, then the findings
numbered, and the ports when they were asked for. It is marked untrusted, because the headers and the banners are words
a stranger wrote: a run that goes on to act on them treats them as it treats any page it read.

## What leaves this Mac

The requests the audit makes: the DNS lookups, one HTTP GET (and at most one more when the first scheme does not
answer), one TLS handshake, and on `depth = "ports"` a connect to each of the 34 ports, which sends nothing but the
TCP handshake. The page's body is not sent to a model by this tool; the run sees the report only, as it sees any other
tool result. The web client's rules stand: public addresses only unless `[web] allow_private_hosts` is true, and the
hosts of `[browser] blocked_hosts` are not reached. The TLS handshake goes to the address that check read, with the
name kept as the TLS server name, so a second lookup cannot move it (DNS rebinding). The port scan connects to the
same checked addresses, so a name that resolves to the local network is refused there too unless the owner allows
private hosts.

## Limits

- No authentication: a site that answers only a signed-in visitor is audited as a visitor sees it.
- No CVE lookup and no exploit; a version is a hint for the owner, not a verdict.
- No payloads: the forms of a host in `owned_hosts` or a scope are tried by `security_scan`, a separate tool ([Security
  scan](security.md)). This audit stays what a browser's visit reads.
- The port list is fixed and short on purpose. A scan of every port is long, noisy and rarely what the owner asked
  for; `nmap` run by hand in a terminal is the tool for that, on a host you may test.
- A passive audit of any public site is allowed because it is what a browser does; it is still a request the site's
  owner sees in their logs.
