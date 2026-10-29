import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP or HTTPS URLs are allowed")
    try:
        addresses = {ipaddress.ip_address(parsed.hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            }
        except socket.gaierror as error:
            raise ValueError(f"Could not resolve host: {parsed.hostname}") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Sitemap URLs must resolve only to public IP addresses")


class PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        validate_public_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def open_public_url(url: str | Request, timeout: int):
    target = url.full_url if isinstance(url, Request) else url
    validate_public_url(target)
    return build_opener(PublicRedirectHandler()).open(url, timeout=timeout)
