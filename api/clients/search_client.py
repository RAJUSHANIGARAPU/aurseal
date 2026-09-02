import requests

# The configured base URL already carries the locale segment
# (``https://www.catawiki.com/en/``), so the search path is ``/s`` and not
# ``/en/s``. Appending the locale a second time produced
# ``https://www.catawiki.com/en//en/s``, which the site's edge rejects — but so
# is a well-formed request from a plain HTTP client, and both come back 403.
# The malformed URL was therefore invisible: the only test covering it asserted
# a 403 and got one for the wrong reason.
SEARCH_PATH = "s"


class SearchClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def search_url(self, keyword):
        """The URL a search would request. Exposed so it can be asserted offline."""
        request = requests.Request(
            "GET",
            f"{self.base_url.rstrip('/')}/{SEARCH_PATH}",
            params={"q": keyword},
        ).prepare()
        return request.url

    def search(self, keyword):
        return requests.get(
            f"{self.base_url.rstrip('/')}/{SEARCH_PATH}",
            params={"q": keyword},
            timeout=10,
        )
