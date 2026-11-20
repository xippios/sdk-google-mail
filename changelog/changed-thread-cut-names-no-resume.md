- A `google_mail_get_thread` answer whose message at `start` alone passes the cap says so
  in its note, `the row at offset <start> alone exceeds the byte cap`, and names no resume
  offset, where it ended `resume with start=<start>`, the start the call had just used.
  Such a message reads alone with `google_mail_get_message`, as the note says.
  (https://github.com/xippios/sdk-core/issues/175)
