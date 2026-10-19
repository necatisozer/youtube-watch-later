"""Edit Watch Later and watch history through YouTube's internal API.

yt-dlp reads the Chrome cookies and signs the requests. The functions use
private yt-dlp helpers (_call_api), so a yt-dlp update can break them.
"""

import threading

from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import YoutubeTabIE

BROWSER = "chrome"
# Without a profile, yt-dlp reads the most recently used Chrome profile,
# which can be signed in to another YouTube account.
PROFILE = "Default"

_lock = threading.Lock()
_ie = None


def _extractor(fresh=False):
    # Reading the Chrome cookies takes a Keychain lookup, so keep one
    # extractor and make a new one only after a failed call.
    global _ie
    if _ie is None or fresh:
        ie = YoutubeTabIE(YoutubeDL({"cookiesfrombrowser": (BROWSER, PROFILE), "quiet": True, "no_warnings": True}))
        ie._real_initialize()
        _ie = ie
    return _ie


def _call(ep, query, video_id):
    with _lock:
        try:
            return _extractor()._call_api(ep, query, video_id, note=False)
        except Exception:
            return _extractor(fresh=True)._call_api(ep, query, video_id, note=False)


def find(node, key):
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for value in node.values():
            yield from find(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from find(value, key)


def remove(video_id):
    response = _call(
        "browse/edit_playlist",
        {
            "playlistId": "WL",
            "actions": [{"action": "ACTION_REMOVE_VIDEO_BY_VIDEO_ID", "removedVideoId": video_id}],
        },
        video_id,
    )
    status = response.get("status")
    if status != "STATUS_SUCCEEDED":
        raise RuntimeError(f"YouTube returned {status}")


def _history_token(video_id, title):
    # The history search returns one lockup per video, with a menu item
    # "Remove from watch history" that carries the feedback token.
    response = _call("browse", {"browseId": "FEhistory", "query": title}, video_id)
    for lockup in find(response, "lockupViewModel"):
        if lockup.get("contentId") != video_id:
            continue
        for item in find(lockup, "listItemViewModel"):
            for endpoint in find(item, "feedbackEndpoint"):
                hides = [c.get("videoId") for c in find(endpoint, "hideItemSectionVideosByIdCommand")]
                if video_id in hides and endpoint.get("feedbackToken"):
                    return endpoint["feedbackToken"]
    return None


def reset(video_id, title):
    """Remove the video from the watch history, which clears its progress."""
    token = _history_token(video_id, title)
    if not token:
        raise RuntimeError("video not found in watch history")
    response = _call(
        "feedback",
        {"feedbackTokens": [token], "isFeedbackTokenUnencrypted": False, "shouldMerge": False},
        video_id,
    )
    responses = response.get("feedbackResponses") or []
    if not responses or not all(r.get("isProcessed") for r in responses):
        raise RuntimeError("YouTube did not process the request")
