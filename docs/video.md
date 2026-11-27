# Watching videos

Dot watches a video file of your Mac or a video at a link (YouTube, X, Instagram, Vimeo and the other sites yt-dlp
reads) with the tool `watch_video` (item 1212, in the tool group `video`). No model Dot runs takes a video, so Dot gives
it the video as pictures and words: frames of what it shows, each with its moment, and what is said in it, written down
with a timestamp each minute. Any model can follow it: one that cannot see has the frames described by the vision model
([Models](models.md)).

Say "watch this and tell me what it says: https://www.youtube.com/watch?v=…", or name a file: "what happens in
~/Movies/demo.mov?".

## Before the first video

- `brew install ffmpeg yt-dlp`. Dot says so when one is missing.
- What is said in a file, or in a link without captions, is written down by the Mac's own dictation model, the one the
  microphone in Dot's composer uses: macOS 26 and Apple's developer tools (`xcode-select --install`). It runs on the
  Mac, needs no key and costs nothing; measured on this Mac, a minute of speech is written down in about 2.5 seconds.

## What one look gives

- **The part looked at.** The whole video, or what `start` and `end` (seconds) name; at most an hour per call. A longer
  video is looked at an hour at a time, and the result says where to go on.
- **Frames.** 16 by default, at most 40, at most one a second, no larger than 768 pixels, taken so that every scene
  of the part is seen (item 1214): Dot looks for the moments the picture changes (a cut, a new slide), takes one frame
  in the middle of each scene and gives the frames left to the longest ones, so a slide shown for three seconds is
  seen. With more scenes than frames (a music video's cuts), none, or scenes of a second or more that cover less than
  half of the part (quick cuts), they are spread evenly; the result says which. Finding the scenes uses the Mac's own
  video decoder and takes a few seconds for a few minutes of video. A closer look at a moment is a call with `start`
  and `end` around it: the same frames over a shorter part. `frames = 0` gives the transcript alone.
- **The transcript.** For a link, the video's own captions when it has some: the ones the uploader wrote in the
  language spoken, else the site's own recognition of the original sound (never a track the site translated). Then the
  video is not downloaded unless frames are asked for. Otherwise the Mac writes down the sound a minute at a time, in
  the language the call names (`language = "tr-TR"`, `"en-US"`); for a link, the language the site gives when the call
  names none.

A link's video is downloaded at most 480 lines high and 2 GB, into a folder of the call's own under the Mac's temporary
folder, which is removed when the call ends: a second look at another part downloads it again (about 9 seconds for a
three-minute video here). Frames and sound are never written to a file. While two other recordings hold the Mac's
dictation model (your microphone, another look), a look waits for one to end, up to five minutes.

## Safety

- A file is read as `read_file` reads one: a file in a protected location (your keys, Dot's own files) is refused.
- A link is an address as `web_fetch`'s is: `[browser] allowed_hosts` and `blocked_hosts` bind it, a long one or one
  with a key in it is refused, Dot's own server is refused, and its host must be on the public internet unless
  `[web] allow_private_hosts`. Before yt-dlp starts, Dot follows the link's redirects through the web client, which
  checks the addresses of every hop it connects to, and refuses the link when any hop is not public, and when the redirects cannot be followed to their end (more than six, a
  hop the rules on addresses turn away or that cannot be reached: the hop nobody looked at may be the private one; item
  1319). Only the link's own first request failing in transit (a timeout, a connection that cannot be made) leaves the link
  to yt-dlp, logged; a site that answers with an error page or turns that client away with a status is no failure of the
  probe, and yt-dlp runs as before.
  yt-dlp then connects by itself: the addresses its extractor takes out of the page (the media's own) are the site's to
  choose and are not seen here.
- ffprobe, ffmpeg and yt-dlp start in the run's sandbox as a shell command does: outside `yolo`, and in every mode
  for a run nobody started (item 2016). ffmpeg opens video
  and sound files only (MP4, MOV, WebM, MKV, AVI, MPEG-TS, WMV, FLV, MP3, WAV and the like) and reaches no address: a
  playlist or a concat list that names other files, whatever it is called, is refused.
- A video is anyone's: the title and the transcript come inside a `<web_content>` fence, and a look marks the run
  untrusted, as a page does ([Safety](safety.md)). So does a look that fails while it runs: what yt-dlp or ffmpeg said may
  be the site's words (an HTTP error's reason), so every such failure is fenced, Dot's own refusals among them (a host
  on the local network, a program not installed). The names of a link's caption
  tracks and its language, which the page writes, are taken only when they are language tags.

A video or a voice message you send Dot in iMessage (up to 200 MB), or attach in the composer or on Dot Next (up to 200 MB, sent as a stream
and written to disk as it arrives, item 1933), is kept with the message and the run is told to watch it with `watch_video` (item 1213,
[Channels](channels.md)); a voice message is written down as a sound.
