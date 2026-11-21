from html.parser import HTMLParser
import json
import urllib.parse
import urllib.request

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "BoopJukebox/1.0 (https://github.com/kamakauzy/boop-jukebox)"


class HeadlinesParser(HTMLParser):
    def __init__(self, display_text):
        super().__init__(convert_charrefs=True)
        self.display_text = display_text
        self.started = False
        self.done = False
        self.depth = 0
        self.bold = 0
        self.text = []
        self.links = []
        self.other_links = []
        self.headlines = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        attrs = dict(attrs)
        if tag == "ul":
            self.depth += 1
            self.started = True
        elif self.started and self.depth == 1 and tag == "li":
            self.text, self.links, self.other_links = [], [], []
        elif self.started and self.depth == 1 and tag in ("b", "strong"):
            self.bold += 1
        elif self.started and self.depth == 1 and tag == "a":
            title = attrs.get("title")
            if title and ":" not in title:
                (self.links if self.bold else self.other_links).append(title)

    def handle_endtag(self, tag):
        if not self.started or self.done:
            return
        if tag == "ul":
            self.depth -= 1
            if self.depth == 0:
                self.done = True
        elif self.depth == 1 and tag in ("b", "strong"):
            self.bold = max(0, self.bold - 1)
        elif self.depth == 1 and tag == "li":
            text = self.display_text(" ".join(self.text))
            # Bold links name the story's main article; other links are image fallbacks.
            articles = list(dict.fromkeys(self.links + self.other_links))[:4]
            if text and articles:
                self.headlines.append((text, articles))

    def handle_data(self, data):
        if self.started and not self.done and self.depth == 1:
            self.text.append(data)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(markup):
    parser = PlainText()
    parser.feed(markup)
    return " ".join(" ".join(parser.parts).split())


def trusted_url(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in (
        "en.wikipedia.org", "commons.wikimedia.org", "upload.wikimedia.org",
        "thumb.wikimedia.org",
    )


class WikimediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted_url(newurl):
            raise ValueError("News response redirected outside Wikimedia")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_url(url, limit):
    if not trusted_url(url):
        raise ValueError("News URL is not a trusted Wikimedia HTTPS host")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(WikimediaRedirect)
    with opener.open(request, timeout=15) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("News response exceeds size limit")
        return data


def query(**params):
    url = API + "?" + urllib.parse.urlencode({"format": "json", **params})
    result = json.loads(read_url(url, 2_000_000))
    if "error" in result:
        raise RuntimeError("Wikimedia API error: " + result["error"].get("info", "unknown"))
    return result


def article_image(article, display_text, artwork_pixels):
    result = query(action="query", prop="pageimages", piprop="name", redirects=1,
                   titles=article)
    page = next(iter(result["query"]["pages"].values()))
    image = page.get("pageimage")
    if not image:
        return None
    result = query(action="query", prop="imageinfo", iiprop="url|extmetadata",
                   iiurlwidth=200, titles="File:" + image)
    page = next(iter(result["query"]["pages"].values()))
    info = page.get("imageinfo", [{}])[0]
    metadata = info.get("extmetadata", {})
    license_name = plain_text(metadata.get("LicenseShortName", {}).get("value", ""))
    artist = plain_text(metadata.get("Artist", {}).get("value", ""))
    normalized_license = license_name.lower()
    license_url = metadata.get("LicenseUrl", {}).get("value", "")
    is_public_domain = normalized_license in ("public domain", "cc0")
    is_creative_commons = normalized_license.startswith(("cc by ", "cc by-sa "))
    if not artist or not (is_public_domain or (is_creative_commons and license_url)):
        return None
    url = info.get("thumburl")
    image_page = info.get("descriptionurl", "")
    if not url or not image_page:
        return None
    image_name = display_text(image.replace("_", " "), None)
    credit = (
        f" | Image: {image_name}, {display_text(artist, None)},"
        f" {display_text(license_name, None)} (resized) | {image_page}"
    )
    if license_url:
        credit += f" | {license_url}"
    if not credit.isascii():
        return None
    return artwork_pixels(read_url(url, 5_000_000)), credit


def fetch_news(display_text, artwork_pixels):
    markup = query(action="parse", page="Template:In_the_news", prop="text")["parse"]["text"]["*"]
    parser = HeadlinesParser(display_text)
    parser.feed(markup)
    if not parser.headlines:
        raise ValueError("Wikimedia news feed contains no headlines")

    stories = []
    for headline, articles in parser.headlines:
        for article in articles:
            found = article_image(article, display_text, artwork_pixels)
            if not found:
                continue
            pixels, credit = found
            banner = (
                f"{headline} | Wikipedia CC BY-SA 4.0: en.wikipedia.org/wiki/Template:In_the_news"
                + credit
            )
            if len(banner) <= 1600:
                stories.append((banner, pixels))
                break
    if not stories:
        raise ValueError("No current headlines have an image with usable attribution")
    return stories
