# Browser page clipper

1. Open `chrome://extensions`, enable Developer mode, and choose **Load unpacked**.
2. Select this `extension/` directory.
3. In Prepare for Offline, open **Settings → Advanced → Extension pairing**.
4. Copy the local app address and app token into the extension, then connect.
5. Choose a context and click **Save this page**.

The extension extracts text in the current tab and sends it directly to the
loopback API. The backend never fetches the URL, which avoids SSRF and keeps the
page content local. Saving a page marks the context as needing preparation; the
last ready pack remains usable until you rebuild.
