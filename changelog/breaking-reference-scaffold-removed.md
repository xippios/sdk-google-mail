- The modules `xippios.google.mail.params` and `xippios.google.mail.enums` are gone, with
  the package's `reference/` directory. They held no list: `params.PACKAGED_LISTS` was
  empty and `enums` defined no enumeration, so a caller that imported either drops the
  import, and `LabelType` and the other value sets stay in `xippios.google.mail.models`.
  (https://github.com/xippios/sdk-google-mail/issues/58)
