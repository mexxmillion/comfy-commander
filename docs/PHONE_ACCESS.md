# Private HTTPS phone access

Tensor Master deliberately listens on `127.0.0.1:8000`. Keep that localhost
binding and put a private HTTPS reverse proxy in front of it. The recommended
route is Tailscale Serve.

Do not use Telnet. Telnet is unencrypted and is not a web proxy. Do not open
port 8000 on the router, bind the controller to `0.0.0.0`, or use Tailscale
Funnel for a normal personal installation. Funnel is public internet exposure;
Serve is restricted to the tailnet.

## One-time setup

1. Install Tailscale on the Tensor Master computer and the phone.
2. Sign both devices into the same tailnet.
3. In the Tailscale admin DNS page, enable MagicDNS and HTTPS certificates if
   they are not already enabled. The first Serve invocation may provide a web
   consent link that enables the missing requirements.
4. Create the Tensor Master admin password once from the host computer at
   `http://127.0.0.1:8000/setup`. Remote devices cannot create the first
   password.

HTTPS certificate names are recorded in public certificate-transparency logs.
Use a non-sensitive Tailscale machine name before enabling HTTPS. The service
and its contents remain private to devices and users allowed by the tailnet.

## Share Tensor Master

From the repository in PowerShell:

```powershell
.\tools\share_phone.ps1
```

Or use Tailscale directly:

```powershell
tailscale serve --bg --yes http://127.0.0.1:8000
tailscale serve status
```

Serve prints an address similar to:

```text
https://tensor-master.example-tailnet.ts.net
```

Open that URL in the phone browser while the Tailscale app is connected. Sign
in with the Tensor Master admin password. The dashboard, outputs, Setup & Models
and studio copilot are responsive on mobile.

No inbound router/firewall port is normally required. Both devices do need
outbound network access, and the host computer, Tensor Master and Tailscale must
remain running.

## Access control

Tailscale identity and ACLs protect network reachability; the Tensor Master
password protects administrative actions in the app. Keep both layers. For a
shared tailnet, restrict the Tensor Master machine to the intended user or
device with Tailscale grants/ACLs.

Do not configure an exit node for this feature. It is unnecessary.

## Stop sharing or troubleshoot

```powershell
tailscale serve status
tailscale status
```

`tailscale serve reset` removes **all** Serve routes configured on that host,
not just Tensor Master. Use it only when that is what you intend.

If the phone cannot connect:

- confirm both devices appear online in `tailscale status`;
- confirm the phone's Tailscale app is connected to the same tailnet;
- confirm `http://127.0.0.1:8000/health` works on the host;
- inspect `tailscale serve status` for the HTTPS URL and proxy target;
- check that tailnet access rules allow the phone or user to reach the host.

For a real public multi-user deployment, use a conventional HTTPS reverse
proxy, explicit authentication policy, backups and monitoring. Do not replace
Serve with Funnel merely to avoid that deployment work.
