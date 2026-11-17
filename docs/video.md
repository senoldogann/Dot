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
- **Frames.** 16 by default, at most 40, at the middle of equal slices of the part, at most one a second, no larger
  than 768 pixels. A closer look at a moment is a call with `start` and `end` around it: the same frames over a shorter
  part. `frames = 0` gives the transcript alone.
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
  `[web] allow_private_hosts`. yt-dlp then connects and follows the site's redirects by itself, which Dot does not see.
- ffprobe, ffmpeg and yt-dlp start in the run's sandbox outside `yolo`, as a shell command does. ffmpeg opens video
  and sound files only (MP4, MOV, WebM, MKV, AVI, MPEG-TS, WMV, FLV, MP3, WAV and the like) and reaches no address: a
  playlist or a concat list that names other files, whatever it is called, is refused.
- A video is anyone's: the title and the transcript come inside a `<web_content>` fence, and a look marks the run
  untrusted, as a page does ([Safety](safety.md)). So does a look that fails once it reached the video: what yt-dlp or
  ffmpeg said may be the site's words (an HTTP error's reason), so it is fenced too. The names of a link's caption
  tracks and its language, which the page writes, are taken only when they are language tags.

A video sent to Dot in iMessage is not read yet: the channel still says it cannot read videos.
