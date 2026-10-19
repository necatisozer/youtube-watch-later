# YouTube Watch Later

A local web app that shows your YouTube Watch Later list. It shows your watch progress, plays videos where you stopped, and removes videos from the list.

The YouTube Data API does not give access to Watch Later. So the app uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) with the cookies of your signed-in Chrome.

## Features

- A grid of your Watch Later videos, with thumbnail, length, channel and views.
- Watch progress on each card, from YouTube's own data.
- Search by title or channel. Filter by channel, length and progress. Sort by playlist order, watch %, length, views or title.
- A player that starts where you stopped. When a video ends, the player offers to remove it from Watch Later.
- **Delete** removes a video from your Watch Later on YouTube.
- **Unwatch** clears the progress of a video. It removes the video from your YouTube watch history.

## Requirements

- macOS with Google Chrome, signed in to YouTube in the Chrome profile "Default".
- yt-dlp from Homebrew:

  ```bash
  brew install yt-dlp
  ```

The server imports yt-dlp as a library. If you start it with another Python, it starts again on the Python that comes with the Homebrew yt-dlp.

## Run

```bash
./server.py 8765
```

Then open <http://localhost:8765>.

On the first fetch, macOS asks for access to "Chrome Safe Storage" in the Keychain. yt-dlp needs this to read the Chrome cookies.

To use another Chrome profile, change `PROFILE` in `wl_edit.py`. To find the profile folder names, look in `~/Library/Application Support/Google/Chrome`.

## How it works

```
browser ──► server.py (127.0.0.1 only)
              ├── Refresh:          yt-dlp command ──► Watch Later list + saved pages with progress
              └── Delete, Unwatch:  wl_edit.py ──► yt-dlp library ──► YouTube internal API
```

- **Refresh** runs `yt-dlp --flat-playlist --write-pages`. The server reads the progress percentages from the saved pages and then deletes them. It keeps the list in `watch_later.json`.
- **Delete** sends the same request as the "Remove from Watch Later" menu item on YouTube.
- **Unwatch** finds the video in your watch history and sends the "Remove from watch history" request.
- The server accepts requests only for the host names `127.0.0.1` and `localhost`. It accepts changes only from its own page. Other websites cannot send requests to it.

## Limits

- `wl_edit.py` uses private yt-dlp functions. A yt-dlp update can break Delete and Unwatch.
- YouTube gives progress only as a whole percentage. The player starts 5 seconds before that point.
- The embedded player does not reliably save your progress to YouTube.
- Your cookies stay in memory. The app does not write them to disk, but `watch_later.json` holds your list. Do not share that file.

## Disclaimer

This project is not affiliated with, endorsed by or sponsored by YouTube or Google. YouTube is a trademark of Google LLC. The app uses internal YouTube endpoints that can change at any time. Use it only with your own account.
