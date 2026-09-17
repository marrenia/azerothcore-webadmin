"""
Minimal SOAP client for the worldserver's remote command interface.

Deleting a character correctly is not a matter of removing rows: the server
returns COD mail to senders, hands off guild leadership, clears group and
arena membership, deletes pets, and drops the character out of its in-memory
caches. Doing that in SQL against a *running* server risks both orphaned data
and the server rewriting rows from cache.

So the panel asks the server to do it, via the same code path the GM command
uses. If the worldserver is down, deletion is refused rather than faked.
"""
import base64
import html
import re
import urllib.error
import urllib.request

SOAP_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<SOAP-ENV:Envelope'
    ' xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
    ' xmlns:xsi="http://www.w3.org/1999/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/1999/XMLSchema"'
    ' xmlns:ns1="urn:AC">'
    "<SOAP-ENV:Body><ns1:executeCommand><command>{cmd}</command>"
    "</ns1:executeCommand></SOAP-ENV:Body></SOAP-ENV:Envelope>"
)


class SoapError(RuntimeError):
    """Raised when the command could not be delivered or the server refused it."""


class SoapClient:
    def __init__(self, cfg):
        self.host = cfg.get("SOAP_HOST", "127.0.0.1")
        self.port = int(cfg.get("SOAP_PORT", 7878))
        self.user = cfg["SOAP_USER"]
        self.password = cfg["SOAP_PASS"]
        self.url = f"http://{self.host}:{self.port}/"

    def _auth_header(self):
        raw = f"{self.user}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def command(self, cmd, timeout=20):
        """Run a server command. Returns the server's text output.

        Raises SoapError with a readable message on any failure.
        """
        body = SOAP_TEMPLATE.format(cmd=html.escape(cmd, quote=True)).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "Authorization": self._auth_header(),
                "SOAPAction": "urn:AC#executeCommand",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            fault = _extract(detail, "faultstring") or f"HTTP {exc.code}"
            if exc.code == 401:
                raise SoapError(
                    "The worldserver rejected the panel's command credentials."
                ) from exc
            raise SoapError(f"Server refused the command: {_clean(fault)}") from exc
        except urllib.error.URLError as exc:
            raise SoapError(
                "Could not reach the worldserver command interface - "
                "the server may be restarting."
            ) from exc

        fault = _extract(payload, "faultstring")
        if fault:
            raise SoapError(_clean(fault))
        return _clean(_extract(payload, "result") or "")


def _extract(xml_text, tag):
    m = re.search(rf"<(?:\w+:)?{tag}[^>]*>(.*?)</(?:\w+:)?{tag}>", xml_text, re.S)
    return m.group(1) if m else None


def _clean(text):
    text = html.unescape(text or "")
    text = text.replace("\r", "")
    return "\n".join(line for line in text.split("\n") if line.strip()).strip()
