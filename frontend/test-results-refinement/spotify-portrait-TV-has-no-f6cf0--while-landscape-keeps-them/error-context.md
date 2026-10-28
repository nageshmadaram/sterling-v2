# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: spotify.spec.ts >> portrait TV has no video overlay controls while landscape keeps them
- Location: e2e/media/spotify.spec.ts:273:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('#player').contentFrame().locator('#tv-now-playing [data-tm-action=play]')
Expected: visible
Error: strict mode violation: locator('#tv-now-playing [data-tm-action=play]') resolved to 2 elements:
    1) <button type="button" class="tm-icon" data-glyph="play" title="Play live TV" aria-pressed="false" data-tm-action="play" aria-label="Play live TV">…</button> aka getByTitle('Play live TV')
    2) <button type="button" data-glyph="play" class="tm-big-play" aria-pressed="false" data-tm-action="play" aria-label="Play live TV" title="Play or pause live TV">…</button> aka getByRole('button', { name: 'Play live TV', exact: true })

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('#player').contentFrame().locator('#tv-now-playing [data-tm-action=play]')

```

# Page snapshot

```yaml
- iframe [active] [ref=e2]:
  - generic [ref=f1e1]:
    - complementary [ref=f1e2]:
      - generic [ref=f1e3]:
        - generic [ref=f1e4]:
          - button [ref=f1e5] [cursor=pointer]:
            - img [ref=f1e6]
          - generic [ref=f1e8]:
            - img [ref=f1e10]
            - generic [ref=f1e12]: StreamOS
        - generic [ref=f1e13]: v18 PRO
      - generic [ref=f1e14]:
        - generic [ref=f1e15]:
          - generic [ref=f1e16]: Visual Experience
          - generic [ref=f1e17]:
            - button "YouTube" [ref=f1e18] [cursor=pointer]
            - button "Netflix" [ref=f1e19] [cursor=pointer]
            - button "Spotify" [ref=f1e20] [cursor=pointer]
        - generic [ref=f1e21]:
          - button "Global Discovery (All)" [ref=f1e22] [cursor=pointer]:
            - img [ref=f1e23]
            - generic [ref=f1e25]: Global Discovery (All)
          - button "Indian Live Hub (All Categories)" [ref=f1e26] [cursor=pointer]:
            - img [ref=f1e27]
            - generic [ref=f1e30]: Indian Live Hub (All Categories)
          - button "Playlists & M3U Sources" [ref=f1e31] [cursor=pointer]:
            - img [ref=f1e32]
            - generic [ref=f1e34]: Playlists & M3U Sources
          - button "Default Boot Stream BBC Earth US Edit" [ref=f1e35] [cursor=pointer]:
            - generic [ref=f1e36]:
              - img [ref=f1e37]
              - generic [ref=f1e39]:
                - paragraph [ref=f1e40]: Default Boot Stream
                - paragraph [ref=f1e41]: BBC Earth US
            - generic [ref=f1e42]: Edit
        - generic [ref=f1e44]:
          - generic [ref=f1e45]: Subscribed
          - generic [ref=f1e46]: "0"
        - generic [ref=f1e47]:
          - generic [ref=f1e48]: Dynamic Languages
          - generic [ref=f1e49]:
            - button "All Global 84" [ref=f1e50] [cursor=pointer]:
              - generic [ref=f1e51]: All Global
              - generic [ref=f1e52]: "84"
            - button "All Indian TV 28" [ref=f1e53] [cursor=pointer]:
              - generic [ref=f1e54]: All Indian TV
              - generic [ref=f1e55]: "28"
            - button "Telugu 28" [ref=f1e56] [cursor=pointer]:
              - generic [ref=f1e57]: Telugu
              - generic [ref=f1e58]: "28"
            - button "Hindi 0" [ref=f1e59] [cursor=pointer]:
              - generic [ref=f1e60]: Hindi
              - generic [ref=f1e61]: "0"
            - button "Tamil 0" [ref=f1e62] [cursor=pointer]:
              - generic [ref=f1e63]: Tamil
              - generic [ref=f1e64]: "0"
            - button "Malayalam 0" [ref=f1e65] [cursor=pointer]:
              - generic [ref=f1e66]: Malayalam
              - generic [ref=f1e67]: "0"
            - button "Kannada 0" [ref=f1e68] [cursor=pointer]:
              - generic [ref=f1e69]: Kannada
              - generic [ref=f1e70]: "0"
            - button "Bengali 0" [ref=f1e71] [cursor=pointer]:
              - generic [ref=f1e72]: Bengali
              - generic [ref=f1e73]: "0"
            - button "Marathi 0" [ref=f1e74] [cursor=pointer]:
              - generic [ref=f1e75]: Marathi
              - generic [ref=f1e76]: "0"
            - button "Punjabi 0" [ref=f1e77] [cursor=pointer]:
              - generic [ref=f1e78]: Punjabi
              - generic [ref=f1e79]: "0"
            - button "Gujarati 0" [ref=f1e80] [cursor=pointer]:
              - generic [ref=f1e81]: Gujarati
              - generic [ref=f1e82]: "0"
            - button "Odia 0" [ref=f1e83] [cursor=pointer]:
              - generic [ref=f1e84]: Odia
              - generic [ref=f1e85]: "0"
            - button "Assamese 0" [ref=f1e86] [cursor=pointer]:
              - generic [ref=f1e87]: Assamese
              - generic [ref=f1e88]: "0"
            - button "Bhojpuri 0" [ref=f1e89] [cursor=pointer]:
              - generic [ref=f1e90]: Bhojpuri
              - generic [ref=f1e91]: "0"
            - button "English 56" [ref=f1e92] [cursor=pointer]:
              - generic [ref=f1e93]: English
              - generic [ref=f1e94]: "56"
            - button "Arabic 0" [ref=f1e95] [cursor=pointer]:
              - generic [ref=f1e96]: Arabic
              - generic [ref=f1e97]: "0"
            - button "Spanish 0" [ref=f1e98] [cursor=pointer]:
              - generic [ref=f1e99]: Spanish
              - generic [ref=f1e100]: "0"
            - button "French 0" [ref=f1e101] [cursor=pointer]:
              - generic [ref=f1e102]: French
              - generic [ref=f1e103]: "0"
            - button "Japanese 0" [ref=f1e104] [cursor=pointer]:
              - generic [ref=f1e105]: Japanese
              - generic [ref=f1e106]: "0"
            - button "Korean 0" [ref=f1e107] [cursor=pointer]:
              - generic [ref=f1e108]: Korean
              - generic [ref=f1e109]: "0"
    - generic [ref=f1e111]:
      - button "TV settings and features" [ref=f1e112] [cursor=pointer]: S
      - generic [ref=f1e113]:
        - button "All" [ref=f1e114] [cursor=pointer]
        - button "Telugu" [ref=f1e115] [cursor=pointer]
        - button "Browse" [pressed] [ref=f1e116] [cursor=pointer]
        - button "Following" [ref=f1e117] [cursor=pointer]
    - main [ref=f1e119]:
      - button "Tap to play live TV" [ref=f1e126] [cursor=pointer]: Channel unavailable. Select another channel
      - generic [ref=f1e127]:
        - generic [ref=f1e128]:
          - generic [ref=f1e129]:
            - button "All Categories 84" [ref=f1e130] [cursor=pointer]:
              - generic [ref=f1e131]: All Categories
              - generic [ref=f1e132]: "84"
            - button "News 42" [ref=f1e133] [cursor=pointer]:
              - generic [ref=f1e134]: News
              - generic [ref=f1e135]: "42"
            - button "Movies 42" [ref=f1e136] [cursor=pointer]:
              - generic [ref=f1e137]: Movies
              - generic [ref=f1e138]: "42"
          - button "Filter channels" [ref=f1e139] [cursor=pointer]:
            - img [ref=f1e140]
        - generic [ref=f1e143]:
          - generic [ref=f1e144]:
            - generic [ref=f1e145]:
              - heading "Popular channels" [level=2] [ref=f1e146]
              - button "Show all" [ref=f1e147] [cursor=pointer]
            - generic [ref=f1e148]:
              - button "Play Channel 01" [active] [ref=f1e149] [cursor=pointer]:
                - strong [ref=f1e150]: Channel 01
                - generic [ref=f1e151]: News · Telugu
              - button "Play Channel 02" [ref=f1e152] [cursor=pointer]:
                - strong [ref=f1e153]: Channel 02
                - generic [ref=f1e154]: Movies · English
              - button "Play Channel 03" [ref=f1e155] [cursor=pointer]:
                - strong [ref=f1e156]: Channel 03
                - generic [ref=f1e157]: News · English
              - button "Play Channel 04" [ref=f1e158] [cursor=pointer]:
                - strong [ref=f1e159]: Channel 04
                - generic [ref=f1e160]: Movies · Telugu
              - button "Play Channel 05" [ref=f1e161] [cursor=pointer]:
                - strong [ref=f1e162]: Channel 05
                - generic [ref=f1e163]: News · English
              - button "Play Channel 06" [ref=f1e164] [cursor=pointer]:
                - strong [ref=f1e165]: Channel 06
                - generic [ref=f1e166]: Movies · English
              - button "Play Channel 07" [ref=f1e167] [cursor=pointer]:
                - strong [ref=f1e168]: Channel 07
                - generic [ref=f1e169]: News · Telugu
              - button "Play Channel 08" [ref=f1e170] [cursor=pointer]:
                - strong [ref=f1e171]: Channel 08
                - generic [ref=f1e172]: Movies · English
              - button "Play Channel 09" [ref=f1e173] [cursor=pointer]:
                - strong [ref=f1e174]: Channel 09
                - generic [ref=f1e175]: News · English
              - button "Play Channel 10" [ref=f1e176] [cursor=pointer]:
                - strong [ref=f1e177]: Channel 10
                - generic [ref=f1e178]: Movies · Telugu
              - button "Play Channel 11" [ref=f1e179] [cursor=pointer]:
                - strong [ref=f1e180]: Channel 11
                - generic [ref=f1e181]: News · English
              - button "Play Channel 12" [ref=f1e182] [cursor=pointer]:
                - strong [ref=f1e183]: Channel 12
                - generic [ref=f1e184]: Movies · English
          - generic [ref=f1e185]:
            - generic [ref=f1e186]:
              - heading "Telugu" [level=2] [ref=f1e187]
              - button "Show all" [ref=f1e188] [cursor=pointer]
            - generic [ref=f1e189]:
              - button "Play Channel 01" [ref=f1e190] [cursor=pointer]:
                - strong [ref=f1e191]: Channel 01
                - generic [ref=f1e192]: News · Telugu
              - button "Play Channel 04" [ref=f1e193] [cursor=pointer]:
                - strong [ref=f1e194]: Channel 04
                - generic [ref=f1e195]: Movies · Telugu
              - button "Play Channel 07" [ref=f1e196] [cursor=pointer]:
                - strong [ref=f1e197]: Channel 07
                - generic [ref=f1e198]: News · Telugu
              - button "Play Channel 10" [ref=f1e199] [cursor=pointer]:
                - strong [ref=f1e200]: Channel 10
                - generic [ref=f1e201]: Movies · Telugu
              - button "Play Channel 13" [ref=f1e202] [cursor=pointer]:
                - strong [ref=f1e203]: Channel 13
                - generic [ref=f1e204]: News · Telugu
              - button "Play Channel 16" [ref=f1e205] [cursor=pointer]:
                - strong [ref=f1e206]: Channel 16
                - generic [ref=f1e207]: Movies · Telugu
              - button "Play Channel 19" [ref=f1e208] [cursor=pointer]:
                - strong [ref=f1e209]: Channel 19
                - generic [ref=f1e210]: News · Telugu
              - button "Play Channel 22" [ref=f1e211] [cursor=pointer]:
                - strong [ref=f1e212]: Channel 22
                - generic [ref=f1e213]: Movies · Telugu
              - button "Play Channel 25" [ref=f1e214] [cursor=pointer]:
                - strong [ref=f1e215]: Channel 25
                - generic [ref=f1e216]: News · Telugu
              - button "Play Channel 28" [ref=f1e217] [cursor=pointer]:
                - strong [ref=f1e218]: Channel 28
                - generic [ref=f1e219]: Movies · Telugu
              - button "Play Channel 31" [ref=f1e220] [cursor=pointer]:
                - strong [ref=f1e221]: Channel 31
                - generic [ref=f1e222]: News · Telugu
              - button "Play Channel 34" [ref=f1e223] [cursor=pointer]:
                - strong [ref=f1e224]: Channel 34
                - generic [ref=f1e225]: Movies · Telugu
          - generic [ref=f1e226]:
            - generic [ref=f1e227]:
              - heading "News" [level=2] [ref=f1e228]
              - button "Show all" [ref=f1e229] [cursor=pointer]
            - generic [ref=f1e230]:
              - button "Play Channel 01" [ref=f1e231] [cursor=pointer]:
                - strong [ref=f1e232]: Channel 01
                - generic [ref=f1e233]: News · Telugu
              - button "Play Channel 03" [ref=f1e234] [cursor=pointer]:
                - strong [ref=f1e235]: Channel 03
                - generic [ref=f1e236]: News · English
              - button "Play Channel 05" [ref=f1e237] [cursor=pointer]:
                - strong [ref=f1e238]: Channel 05
                - generic [ref=f1e239]: News · English
              - button "Play Channel 07" [ref=f1e240] [cursor=pointer]:
                - strong [ref=f1e241]: Channel 07
                - generic [ref=f1e242]: News · Telugu
              - button "Play Channel 09" [ref=f1e243] [cursor=pointer]:
                - strong [ref=f1e244]: Channel 09
                - generic [ref=f1e245]: News · English
              - button "Play Channel 11" [ref=f1e246] [cursor=pointer]:
                - strong [ref=f1e247]: Channel 11
                - generic [ref=f1e248]: News · English
              - button "Play Channel 13" [ref=f1e249] [cursor=pointer]:
                - strong [ref=f1e250]: Channel 13
                - generic [ref=f1e251]: News · Telugu
              - button "Play Channel 15" [ref=f1e252] [cursor=pointer]:
                - strong [ref=f1e253]: Channel 15
                - generic [ref=f1e254]: News · English
              - button "Play Channel 17" [ref=f1e255] [cursor=pointer]:
                - strong [ref=f1e256]: Channel 17
                - generic [ref=f1e257]: News · English
              - button "Play Channel 19" [ref=f1e258] [cursor=pointer]:
                - strong [ref=f1e259]: Channel 19
                - generic [ref=f1e260]: News · Telugu
              - button "Play Channel 21" [ref=f1e261] [cursor=pointer]:
                - strong [ref=f1e262]: Channel 21
                - generic [ref=f1e263]: News · English
              - button "Play Channel 23" [ref=f1e264] [cursor=pointer]:
                - strong [ref=f1e265]: Channel 23
                - generic [ref=f1e266]: News · English
          - generic [ref=f1e267]:
            - generic [ref=f1e268]:
              - heading "Movies" [level=2] [ref=f1e269]
              - button "Show all" [ref=f1e270] [cursor=pointer]
            - generic [ref=f1e271]:
              - button "Play Channel 02" [ref=f1e272] [cursor=pointer]:
                - strong [ref=f1e273]: Channel 02
                - generic [ref=f1e274]: Movies · English
              - button "Play Channel 04" [ref=f1e275] [cursor=pointer]:
                - strong [ref=f1e276]: Channel 04
                - generic [ref=f1e277]: Movies · Telugu
              - button "Play Channel 06" [ref=f1e278] [cursor=pointer]:
                - strong [ref=f1e279]: Channel 06
                - generic [ref=f1e280]: Movies · English
              - button "Play Channel 08" [ref=f1e281] [cursor=pointer]:
                - strong [ref=f1e282]: Channel 08
                - generic [ref=f1e283]: Movies · English
              - button "Play Channel 10" [ref=f1e284] [cursor=pointer]:
                - strong [ref=f1e285]: Channel 10
                - generic [ref=f1e286]: Movies · Telugu
              - button "Play Channel 12" [ref=f1e287] [cursor=pointer]:
                - strong [ref=f1e288]: Channel 12
                - generic [ref=f1e289]: Movies · English
              - button "Play Channel 14" [ref=f1e290] [cursor=pointer]:
                - strong [ref=f1e291]: Channel 14
                - generic [ref=f1e292]: Movies · English
              - button "Play Channel 16" [ref=f1e293] [cursor=pointer]:
                - strong [ref=f1e294]: Channel 16
                - generic [ref=f1e295]: Movies · Telugu
              - button "Play Channel 18" [ref=f1e296] [cursor=pointer]:
                - strong [ref=f1e297]: Channel 18
                - generic [ref=f1e298]: Movies · English
              - button "Play Channel 20" [ref=f1e299] [cursor=pointer]:
                - strong [ref=f1e300]: Channel 20
                - generic [ref=f1e301]: Movies · English
              - button "Play Channel 22" [ref=f1e302] [cursor=pointer]:
                - strong [ref=f1e303]: Channel 22
                - generic [ref=f1e304]: Movies · Telugu
              - button "Play Channel 24" [ref=f1e305] [cursor=pointer]:
                - strong [ref=f1e306]: Channel 24
                - generic [ref=f1e307]: Movies · English
          - generic [ref=f1e308]:
            - generic [ref=f1e309]:
              - heading "English" [level=2] [ref=f1e310]
              - button "Show all" [ref=f1e311] [cursor=pointer]
            - generic [ref=f1e312]:
              - button "Play Channel 02" [ref=f1e313] [cursor=pointer]:
                - strong [ref=f1e314]: Channel 02
                - generic [ref=f1e315]: Movies · English
              - button "Play Channel 03" [ref=f1e316] [cursor=pointer]:
                - strong [ref=f1e317]: Channel 03
                - generic [ref=f1e318]: News · English
              - button "Play Channel 05" [ref=f1e319] [cursor=pointer]:
                - strong [ref=f1e320]: Channel 05
                - generic [ref=f1e321]: News · English
              - button "Play Channel 06" [ref=f1e322] [cursor=pointer]:
                - strong [ref=f1e323]: Channel 06
                - generic [ref=f1e324]: Movies · English
              - button "Play Channel 08" [ref=f1e325] [cursor=pointer]:
                - strong [ref=f1e326]: Channel 08
                - generic [ref=f1e327]: Movies · English
              - button "Play Channel 09" [ref=f1e328] [cursor=pointer]:
                - strong [ref=f1e329]: Channel 09
                - generic [ref=f1e330]: News · English
              - button "Play Channel 11" [ref=f1e331] [cursor=pointer]:
                - strong [ref=f1e332]: Channel 11
                - generic [ref=f1e333]: News · English
              - button "Play Channel 12" [ref=f1e334] [cursor=pointer]:
                - strong [ref=f1e335]: Channel 12
                - generic [ref=f1e336]: Movies · English
              - button "Play Channel 14" [ref=f1e337] [cursor=pointer]:
                - strong [ref=f1e338]: Channel 14
                - generic [ref=f1e339]: Movies · English
              - button "Play Channel 15" [ref=f1e340] [cursor=pointer]:
                - strong [ref=f1e341]: Channel 15
                - generic [ref=f1e342]: News · English
              - button "Play Channel 17" [ref=f1e343] [cursor=pointer]:
                - strong [ref=f1e344]: Channel 17
                - generic [ref=f1e345]: News · English
              - button "Play Channel 18" [ref=f1e346] [cursor=pointer]:
                - strong [ref=f1e347]: Channel 18
                - generic [ref=f1e348]: Movies · English
          - generic [ref=f1e349]:
            - generic [ref=f1e350]:
              - heading "All 84 channels" [level=2] [ref=f1e351]
              - button "Show all" [ref=f1e352] [cursor=pointer]
            - generic [ref=f1e353]:
              - button "Play Channel 01" [ref=f1e354] [cursor=pointer]:
                - strong [ref=f1e355]: Channel 01
                - generic [ref=f1e356]: News · Telugu
              - button "Play Channel 02" [ref=f1e357] [cursor=pointer]:
                - strong [ref=f1e358]: Channel 02
                - generic [ref=f1e359]: Movies · English
              - button "Play Channel 03" [ref=f1e360] [cursor=pointer]:
                - strong [ref=f1e361]: Channel 03
                - generic [ref=f1e362]: News · English
              - button "Play Channel 04" [ref=f1e363] [cursor=pointer]:
                - strong [ref=f1e364]: Channel 04
                - generic [ref=f1e365]: Movies · Telugu
              - button "Play Channel 05" [ref=f1e366] [cursor=pointer]:
                - strong [ref=f1e367]: Channel 05
                - generic [ref=f1e368]: News · English
              - button "Play Channel 06" [ref=f1e369] [cursor=pointer]:
                - strong [ref=f1e370]: Channel 06
                - generic [ref=f1e371]: Movies · English
              - button "Play Channel 07" [ref=f1e372] [cursor=pointer]:
                - strong [ref=f1e373]: Channel 07
                - generic [ref=f1e374]: News · Telugu
              - button "Play Channel 08" [ref=f1e375] [cursor=pointer]:
                - strong [ref=f1e376]: Channel 08
                - generic [ref=f1e377]: Movies · English
              - button "Play Channel 09" [ref=f1e378] [cursor=pointer]:
                - strong [ref=f1e379]: Channel 09
                - generic [ref=f1e380]: News · English
              - button "Play Channel 10" [ref=f1e381] [cursor=pointer]:
                - strong [ref=f1e382]: Channel 10
                - generic [ref=f1e383]: Movies · Telugu
              - button "Play Channel 11" [ref=f1e384] [cursor=pointer]:
                - strong [ref=f1e385]: Channel 11
                - generic [ref=f1e386]: News · English
              - button "Play Channel 12" [ref=f1e387] [cursor=pointer]:
                - strong [ref=f1e388]: Channel 12
                - generic [ref=f1e389]: Movies · English
    - dialog "Now Playing" [ref=f1e390]:
      - generic [ref=f1e391]:
        - generic [ref=f1e392]:
          - button "Close Now Playing" [ref=f1e393] [cursor=pointer]:
            - img [ref=f1e394]
          - generic [ref=f1e396]:
            - generic [ref=f1e397]: PLAYING FROM
            - strong [ref=f1e398]: News
          - button "Now Playing options" [ref=f1e399] [cursor=pointer]:
            - img [ref=f1e400]
        - generic [ref=f1e402]:
          - generic [ref=f1e403]:
            - heading "Channel 01" [level=2] [ref=f1e404]
            - paragraph [ref=f1e405]: News · Telugu
          - button "Save channel to your library" [ref=f1e406] [cursor=pointer]:
            - img [ref=f1e407]
        - generic [ref=f1e411]:
          - generic [ref=f1e412]: Live broadcast
          - generic [ref=f1e413]: LIVE
        - generic [ref=f1e414]:
          - button "Unmute TV" [ref=f1e415] [cursor=pointer]:
            - img [ref=f1e416]
          - button "Previous channel" [ref=f1e418] [cursor=pointer]:
            - img [ref=f1e419]
          - button "Play live TV" [ref=f1e421] [cursor=pointer]:
            - img [ref=f1e422]
          - button "Next channel" [ref=f1e424] [cursor=pointer]:
            - img [ref=f1e425]
          - button "Picture in picture" [ref=f1e427] [cursor=pointer]:
            - img [ref=f1e428]
        - generic [ref=f1e430]:
          - button "Surprise channel" [ref=f1e431] [cursor=pointer]:
            - img [ref=f1e432]
          - generic [ref=f1e434]:
            - button "Channel information and programme guide" [ref=f1e435] [cursor=pointer]:
              - img [ref=f1e436]
            - button "Open channel queue" [ref=f1e438] [cursor=pointer]:
              - img [ref=f1e439]
            - button "Set sleep timer" [ref=f1e441] [cursor=pointer]: ◷
      - region "About this channel" [ref=f1e442]:
        - heading "About this channel" [level=3] [ref=f1e444]
        - strong [ref=f1e445]: Channel 01
        - paragraph [ref=f1e446]: News · Telugu
        - paragraph [ref=f1e447]: Live broadcast · Programme information depends on the broadcaster
      - region "Up next" [ref=f1e448]:
        - generic [ref=f1e449]:
          - heading "Up next" [level=3] [ref=f1e450]
          - button "Open queue" [ref=f1e451] [cursor=pointer]
        - generic [ref=f1e452]:
          - button "Channel 02 Movies · English" [ref=f1e453] [cursor=pointer]:
            - generic [ref=f1e454]:
              - strong [ref=f1e455]: Channel 02
              - generic [ref=f1e456]: Movies · English
          - button "Channel 03 News · English" [ref=f1e457] [cursor=pointer]:
            - generic [ref=f1e458]:
              - strong [ref=f1e459]: Channel 03
              - generic [ref=f1e460]: News · English
          - button "Channel 04 Movies · Telugu" [ref=f1e461] [cursor=pointer]:
            - generic [ref=f1e462]:
              - strong [ref=f1e463]: Channel 04
              - generic [ref=f1e464]: Movies · Telugu
          - button "Channel 05 News · English" [ref=f1e465] [cursor=pointer]:
            - generic [ref=f1e466]:
              - strong [ref=f1e467]: Channel 05
              - generic [ref=f1e468]: News · English
          - button "Channel 06 Movies · English" [ref=f1e469] [cursor=pointer]:
            - generic [ref=f1e470]:
              - strong [ref=f1e471]: Channel 06
              - generic [ref=f1e472]: Movies · English
      - region "More News · 41 channels" [ref=f1e473]:
        - heading "More News · 41 channels" [level=3] [ref=f1e475]
        - generic [ref=f1e476]:
          - button "Channel 07 News · Telugu" [ref=f1e477] [cursor=pointer]:
            - generic [ref=f1e478]:
              - strong [ref=f1e479]: Channel 07
              - generic [ref=f1e480]: News · Telugu
          - button "Channel 13 News · Telugu" [ref=f1e481] [cursor=pointer]:
            - generic [ref=f1e482]:
              - strong [ref=f1e483]: Channel 13
              - generic [ref=f1e484]: News · Telugu
          - button "Channel 19 News · Telugu" [ref=f1e485] [cursor=pointer]:
            - generic [ref=f1e486]:
              - strong [ref=f1e487]: Channel 19
              - generic [ref=f1e488]: News · Telugu
          - button "Channel 25 News · Telugu" [ref=f1e489] [cursor=pointer]:
            - generic [ref=f1e490]:
              - strong [ref=f1e491]: Channel 25
              - generic [ref=f1e492]: News · Telugu
          - button "Channel 31 News · Telugu" [ref=f1e493] [cursor=pointer]:
            - generic [ref=f1e494]:
              - strong [ref=f1e495]: Channel 31
              - generic [ref=f1e496]: News · Telugu
          - button "Channel 37 News · Telugu" [ref=f1e497] [cursor=pointer]:
            - generic [ref=f1e498]:
              - strong [ref=f1e499]: Channel 37
              - generic [ref=f1e500]: News · Telugu
          - button "Channel 43 News · Telugu" [ref=f1e501] [cursor=pointer]:
            - generic [ref=f1e502]:
              - strong [ref=f1e503]: Channel 43
              - generic [ref=f1e504]: News · Telugu
          - button "Channel 49 News · Telugu" [ref=f1e505] [cursor=pointer]:
            - generic [ref=f1e506]:
              - strong [ref=f1e507]: Channel 49
              - generic [ref=f1e508]: News · Telugu
          - button "Channel 55 News · Telugu" [ref=f1e509] [cursor=pointer]:
            - generic [ref=f1e510]:
              - strong [ref=f1e511]: Channel 55
              - generic [ref=f1e512]: News · Telugu
          - button "Channel 61 News · Telugu" [ref=f1e513] [cursor=pointer]:
            - generic [ref=f1e514]:
              - strong [ref=f1e515]: Channel 61
              - generic [ref=f1e516]: News · Telugu
          - button "Channel 67 News · Telugu" [ref=f1e517] [cursor=pointer]:
            - generic [ref=f1e518]:
              - strong [ref=f1e519]: Channel 67
              - generic [ref=f1e520]: News · Telugu
          - button "Channel 73 News · Telugu" [ref=f1e521] [cursor=pointer]:
            - generic [ref=f1e522]:
              - strong [ref=f1e523]: Channel 73
              - generic [ref=f1e524]: News · Telugu
          - button "Channel 79 News · Telugu" [ref=f1e525] [cursor=pointer]:
            - generic [ref=f1e526]:
              - strong [ref=f1e527]: Channel 79
              - generic [ref=f1e528]: News · Telugu
          - button "Channel 03 News · English" [ref=f1e529] [cursor=pointer]:
            - generic [ref=f1e530]:
              - strong [ref=f1e531]: Channel 03
              - generic [ref=f1e532]: News · English
          - button "Channel 05 News · English" [ref=f1e533] [cursor=pointer]:
            - generic [ref=f1e534]:
              - strong [ref=f1e535]: Channel 05
              - generic [ref=f1e536]: News · English
          - button "Channel 09 News · English" [ref=f1e537] [cursor=pointer]:
            - generic [ref=f1e538]:
              - strong [ref=f1e539]: Channel 09
              - generic [ref=f1e540]: News · English
          - button "Channel 11 News · English" [ref=f1e541] [cursor=pointer]:
            - generic [ref=f1e542]:
              - strong [ref=f1e543]: Channel 11
              - generic [ref=f1e544]: News · English
          - button "Channel 15 News · English" [ref=f1e545] [cursor=pointer]:
            - generic [ref=f1e546]:
              - strong [ref=f1e547]: Channel 15
              - generic [ref=f1e548]: News · English
          - button "Channel 17 News · English" [ref=f1e549] [cursor=pointer]:
            - generic [ref=f1e550]:
              - strong [ref=f1e551]: Channel 17
              - generic [ref=f1e552]: News · English
          - button "Channel 21 News · English" [ref=f1e553] [cursor=pointer]:
            - generic [ref=f1e554]:
              - strong [ref=f1e555]: Channel 21
              - generic [ref=f1e556]: News · English
          - button "Channel 23 News · English" [ref=f1e557] [cursor=pointer]:
            - generic [ref=f1e558]:
              - strong [ref=f1e559]: Channel 23
              - generic [ref=f1e560]: News · English
          - button "Channel 27 News · English" [ref=f1e561] [cursor=pointer]:
            - generic [ref=f1e562]:
              - strong [ref=f1e563]: Channel 27
              - generic [ref=f1e564]: News · English
          - button "Channel 29 News · English" [ref=f1e565] [cursor=pointer]:
            - generic [ref=f1e566]:
              - strong [ref=f1e567]: Channel 29
              - generic [ref=f1e568]: News · English
          - button "Channel 33 News · English" [ref=f1e569] [cursor=pointer]:
            - generic [ref=f1e570]:
              - strong [ref=f1e571]: Channel 33
              - generic [ref=f1e572]: News · English
          - button "Channel 35 News · English" [ref=f1e573] [cursor=pointer]:
            - generic [ref=f1e574]:
              - strong [ref=f1e575]: Channel 35
              - generic [ref=f1e576]: News · English
          - button "Channel 39 News · English" [ref=f1e577] [cursor=pointer]:
            - generic [ref=f1e578]:
              - strong [ref=f1e579]: Channel 39
              - generic [ref=f1e580]: News · English
          - button "Channel 41 News · English" [ref=f1e581] [cursor=pointer]:
            - generic [ref=f1e582]:
              - strong [ref=f1e583]: Channel 41
              - generic [ref=f1e584]: News · English
          - button "Channel 45 News · English" [ref=f1e585] [cursor=pointer]:
            - generic [ref=f1e586]:
              - strong [ref=f1e587]: Channel 45
              - generic [ref=f1e588]: News · English
          - button "Channel 47 News · English" [ref=f1e589] [cursor=pointer]:
            - generic [ref=f1e590]:
              - strong [ref=f1e591]: Channel 47
              - generic [ref=f1e592]: News · English
          - button "Channel 51 News · English" [ref=f1e593] [cursor=pointer]:
            - generic [ref=f1e594]:
              - strong [ref=f1e595]: Channel 51
              - generic [ref=f1e596]: News · English
        - button "Show more" [ref=f1e597] [cursor=pointer]
    - status "Pull to refresh TV": ↻
    - status
```

# Test source

```ts
  177 |  await frame.locator('#tv-mobile-shell .tm-avatar').click();
  178 |  await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
  179 |  await frame.locator('#tv-mobile-sheet [data-tm-filter=category]').selectOption('News');
  180 |  await expect(frame.locator('#catalog-showing-count')).toContainText('of 42 channels');
  181 |  await frame.locator('#tv-mobile-shell .tm-avatar').click();
  182 |  await frame.locator('#tv-mobile-sheet [data-tm-action=indian]').click();
  183 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (28)');
  184 |  await expect(frame.locator('#catalog-showing-count')).toContainText('of 28 channels');
  185 | });
  186 | 
  187 | test('TV menu resizes and player exposes honest programme info and instant PiP',async({page})=>{
  188 |  const frame=await seededTv(page);
  189 |  await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
  190 |  await frame.locator('#tv-now-playing [data-tm-action="more"]').click();
  191 |  const menu=frame.locator('#tv-mobile-sheet');
  192 |  const height=menu.getByRole('slider',{name:'Menu height'});
  193 |  await expect(height).toHaveAttribute('aria-valuenow','72');
  194 |  await menu.getByRole('button',{name:'Expand menu',exact:true}).click();
  195 |  await expect(height).toHaveAttribute('aria-valuenow','95');
  196 |  await height.focus();await height.press('ArrowDown');
  197 |  await expect(height).toHaveAttribute('aria-valuenow','85');
  198 |  await menu.getByRole('button',{name:'Close TV features',exact:true}).click();
  199 |  await frame.locator('#tv-now-playing [data-tm-action="epg"]').click();
  200 |  await expect(menu).toContainText('Programme schedule not supplied by this broadcaster');
  201 |  await menu.getByRole('button',{name:'Close TV features',exact:true}).click();
  202 |  await expect(frame.locator('#tv-now-playing [data-tm-action="fullscreen"]')).toHaveCount(0);
  203 |  await expect(frame.locator('#tv-now-playing [data-tm-action="pip"]')).toBeVisible();
  204 |  await frame.locator('body').evaluate(()=>{
  205 |   const video=document.getElementById('video-player') as any;
  206 |   Object.defineProperty(video,'readyState',{configurable:true,get:()=>2});
  207 |   video.requestPictureInPicture=()=>{document.body.dataset.testPip='requested';return Promise.resolve();};
  208 |  });
  209 |  await frame.locator('#tv-now-playing [data-tm-action="pip"]').click();
  210 |  await expect(frame.locator('body')).toHaveAttribute('data-test-pip','requested');
  211 |  await page.clock.install();
  212 |  // Leave the full-screen sheet before interacting with the underlying video.
  213 |  await frame.locator('#tv-now-playing [data-tm-action=collapse]').click();
  214 |  await frame.locator('#video-surface-card').dispatchEvent('pointermove');
  215 |  await expect(frame.locator('#custom-video-hud')).not.toHaveClass(/mobile-hud-hidden/);
  216 |  await page.clock.fastForward(2700);
  217 |  await expect(frame.locator('#custom-video-hud')).toHaveClass(/mobile-hud-hidden/);
  218 |  await frame.locator('#video-surface-card').dispatchEvent('pointerdown');
  219 |  await expect(frame.locator('#custom-video-hud')).not.toHaveClass(/mobile-hud-hidden/);
  220 | });
  221 | 
  222 | for(const theme of ['spotify','youtube','netflix']){
  223 |  test(`${theme} bottom scrolling keeps filter rows stable and preserves loaded cards`,async({page})=>{
  224 |   const frame=await seededTv(page);
  225 |   await frame.locator('body').evaluate((theme)=>{(window as any).eval(`switchTheme(${JSON.stringify(theme)});renderViewport();`);},theme);
  226 |   const before=await frame.locator('body').evaluate(()=>{
  227 |    const row=document.getElementById('sticky-chip-bar')!;
  228 |    const first=document.querySelector('#yt-studio-grid .channel-grid-card')!;
  229 |    (window as any).__firstCard=first;
  230 |    (window as any).__imageWrites=0;
  231 |    new MutationObserver(records=>{(window as any).__imageWrites+=records.length;}).observe(first,{subtree:true,attributes:true,attributeFilter:['src']});
  232 |    (window as any).__scrollTransitions=0;
  233 |    let hidden=document.body.classList.contains('tm-chips-hidden');
  234 |    new MutationObserver(()=>{const next=document.body.classList.contains('tm-chips-hidden');if(next!==hidden){(window as any).__scrollTransitions++;hidden=next;}}).observe(document.body,{attributes:true,attributeFilter:['class']});
  235 |    return {height:row.getBoundingClientRect().height,count:document.querySelectorAll('#yt-studio-grid .channel-grid-card').length};
  236 |   });
  237 |   const main=frame.locator('#main-scroll');
  238 |   // Cross the old collapse threshold in both directions, then reach pagination.
  239 |   for(const top of [90,50,120,70]){
  240 |    await main.evaluate((el,top)=>el.scrollTo({top,behavior:'instant'}),top);
  241 |    await page.waitForTimeout(40);
  242 |   }
  243 |   await main.evaluate(el=>el.scrollTo({top:el.scrollHeight,behavior:'instant'}));
  244 |   await expect.poll(()=>frame.locator('#yt-studio-grid .channel-grid-card').count()).toBeGreaterThan(before.count);
  245 |   await page.waitForTimeout(200);
  246 |   const after=await frame.locator('body').evaluate(()=>({
  247 |    height:document.getElementById('sticky-chip-bar')!.getBoundingClientRect().height,
  248 |    sameCard:(window as any).__firstCard===document.querySelector('#yt-studio-grid .channel-grid-card'),
  249 |    imageWrites:(window as any).__imageWrites,transitions:(window as any).__scrollTransitions
  250 |   }));
  251 |   expect(after.height).toBe(before.height);expect(after.sameCard).toBe(true);
  252 |   expect(after.imageWrites).toBe(0);expect(after.transitions).toBe(0);
  253 |  });
  254 | }
  255 | 
  256 | test('TV availability filter hides exhausted failures, persists and restores recovered channels',async({page})=>{
  257 |  const frame=await seededTv(page);
  258 |  await frame.locator('body').evaluate(()=>{(window as any).eval(`activeChannel=allChannels[1];startBackupFallback(activeChannel,document.getElementById('video-player'),document.getElementById('player-spinner'),document.getElementById('server-badge'));`);});
  259 |  await frame.locator('#tv-mobile-shell .tm-avatar').click();
  260 |  await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
  261 |  await frame.locator('[data-tm-filter=availability]').selectOption('available');
  262 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (83)');
  263 |  expect(await frame.locator('body').evaluate(()=>JSON.parse((window as any).eval('tvStorage.getItem("streamos_ui_v14")')).hideUnavailable)).toBe(true);
  264 |  expect(await frame.locator('body').evaluate(()=>(window as any).eval('getRankedSearchMatches("Channel 02").length'))).toBe(0);
  265 |  await frame.locator('#tv-mobile-shell .tm-avatar').click();
  266 |  await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
  267 |  await frame.locator('[data-tm-filter=availability]').selectOption('all');
  268 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
  269 |  await frame.locator('body').evaluate(()=>{(window as any).eval(`activeChannel=allChannels[1];document.getElementById('video-player').dispatchEvent(new Event('playing'));`);});
  270 |  expect(await frame.locator('body').evaluate(()=>(window as any).eval('unavailableChannels.has(allChannels[1].url)'))).toBe(false);
  271 | });
  272 | 
  273 | test('portrait TV has no video overlay controls while landscape keeps them',async({page})=>{
  274 |  const frame=await seededTv(page);
  275 |  await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
  276 |  await expect(frame.locator('#custom-video-hud')).toBeHidden();
> 277 |  await expect(frame.locator('#tv-now-playing [data-tm-action=play]')).toBeVisible();
      |                                                                       ^ Error: expect(locator).toBeVisible() failed
  278 |  await page.setViewportSize({width:844,height:390});
  279 |  // Leave the full-screen sheet before interacting with the underlying video.
  280 |  await frame.locator('#tv-now-playing [data-tm-action=collapse]').click();
  281 |  await frame.locator('#video-surface-card').dispatchEvent('pointermove');
  282 |  await expect(frame.locator('#custom-video-hud')).toBeVisible();
  283 | });
  284 | 
  285 | test('unavailable channels expire after three hours and can be retried immediately',async({page})=>{
  286 |  await page.clock.install();
  287 |  const frame=await seededTv(page);
  288 |  await frame.locator('body').evaluate(()=>{(window as any).eval('hideUnavailableChannels=true;markChannelUnavailable(allChannels[1]);');});
  289 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (83)');
  290 |  await frame.locator('#tv-mobile-shell .tm-avatar').click();
  291 |  await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
  292 |  await frame.getByRole('button',{name:'Retry unavailable channels now',exact:true}).click();
  293 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
  294 |  expect(await frame.locator('body').evaluate(()=>(window as any).eval('hideUnavailableChannels'))).toBe(true);
  295 |  await frame.locator('body').evaluate(()=>{(window as any).eval('markChannelUnavailable(allChannels[1]);');});
  296 |  await page.clock.fastForward(2*60*60*1000);
  297 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (83)');
  298 |  await page.clock.fastForward(60*60*1000+60001);
  299 |  await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
  300 | });
  301 | 
  302 | test('unchanged discovery views preserve existing cards and home shelves',async({page})=>{
  303 |  const frame=await seededTv(page);
  304 |  const result=await frame.locator('body').evaluate(()=>{return (window as any).eval(`
  305 |   renderNextBatch();const first=document.querySelector('#yt-studio-grid .channel-grid-card');
  306 |   const count=renderedItems;
  307 |   renderViewport();renderViewport();
  308 |   const stableGrid=first===document.querySelector('#yt-studio-grid .channel-grid-card')&&renderedItems===count;
  309 |   document.querySelector('#tv-mobile-shell .tm-tabs [data-tm-action=home]').click();
  310 |   const shelf=document.querySelector('#tv-mobile-home section');
  311 |   renderViewport();renderViewport();
  312 |   ({stableGrid,stableShelf:shelf===document.querySelector('#tv-mobile-home section')});
  313 |  `);});
  314 |  expect(result).toEqual({stableGrid:true,stableShelf:true});
  315 | });
  316 | 
  317 | test('feature menus restore focus to the player instead of their removed controls',async({page})=>{
  318 |  const frame=await seededTv(page);
  319 |  await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
  320 |  const more=frame.locator('#tv-now-playing [data-tm-action=more]');await more.click();
  321 |  await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
  322 |  await frame.locator('#tv-mobile-sheet [data-tm-action=close-sheet]').click();
  323 |  await expect(more).toBeFocused();
  324 | });
  325 | 
  326 | test('comfort sheet padding is not treated as a backdrop dismissal',async({page})=>{
  327 |  const frame=await seededTv(page);
  328 |  await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
  329 |  await frame.getByRole('button',{name:'Set sleep timer',exact:true}).click();
  330 |  const dialog=frame.getByRole('dialog',{name:'Wind down'});await expect(dialog).toBeVisible();
  331 |  await dialog.click({position:{x:4,y:6}});await expect(dialog).toBeVisible();
  332 |  await dialog.getByRole('button',{name:'Close sleep timer'}).click();await expect(dialog).toBeHidden();
  333 | });
  334 | 
  335 | test('standalone TV boots without a host, persists settings and tolerates damaged libraries',async({page})=>{
  336 |  await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  337 |  const html=readFileSync(resolve(process.cwd(),'../sterling-iptv/sterling-iptv.html'),'utf8');
  338 |  await page.route('**/__standalone_tv__',route=>route.fulfill({contentType:'text/html',body:html}));
  339 |  await page.addInitScript(()=>{
  340 |   localStorage.setItem('sterling.tv.streamos_theme_v14','spotify');
  341 |   localStorage.setItem('sterling.tv.streamos_startup_channel_v14',JSON.stringify({name:'Audit standalone',url:'https://fixture.test/live.m3u8'}));
  342 |   localStorage.setItem('sterling.tv.streamos_favs_v14','[null,5,{}]');
  343 |   localStorage.setItem('sterling.tv.streamos_stats_v14','broken JSON');
  344 |  });
  345 |  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  346 |  await page.goto('/__standalone_tv__');
  347 |  await expect(page.locator('#tm-now-title')).toHaveText('Audit standalone');
  348 |  await page.evaluate(()=>{(window as any).setChannelAvailabilityFilter('available');});
  349 |  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('sterling.tv.streamos_ui_v14')||'{}').hideUnavailable)).toBe(true);
  350 |  await page.reload();await expect(page.locator('#catalog-availability-select')).toHaveValue('available');
  351 |  expect(errors).toEqual([]);
  352 | });
  353 | 
  354 | for(const kind of ['tv','radio'] as const)test(`${kind} offline notices support touch dismissal and reconnect`,async({page})=>{
  355 |  const frame=await openPlayer(page,kind);
  356 |  await frame.locator('body').evaluate(()=>{
  357 |   Object.defineProperty(navigator,'onLine',{value:false,configurable:true});window.dispatchEvent(new Event('offline'));
  358 |  });
  359 |  const notice=frame.locator('.media-connection');await expect(notice).toBeVisible();
  360 |  await notice.getByRole('button',{name:'Dismiss offline notice'}).click();await expect(notice).toBeHidden();
  361 |  await frame.locator('body').evaluate(()=>{
  362 |   Object.defineProperty(navigator,'onLine',{value:true,configurable:true});window.dispatchEvent(new Event('online'));
  363 |   Object.defineProperty(navigator,'onLine',{value:false,configurable:true});window.dispatchEvent(new Event('offline'));
  364 |  });
  365 |  await expect(notice).toBeVisible();
  366 |  const bounds=await notice.boundingBox();if(!bounds)throw Error('Missing offline notice');
  367 |  await page.mouse.move(bounds.x+12,bounds.y+bounds.height/2);await page.mouse.down();
  368 |  await page.mouse.move(bounds.x+100,bounds.y+bounds.height/2);await page.mouse.up();
  369 |  await expect(notice).toBeHidden();
  370 | });
  371 | 
  372 | for(const kind of ['tv','radio'] as const)test(`${kind} echoes bounded search request IDs through the real player`,async({page})=>{
  373 |  await openPlayer(page,kind);
  374 |  await page.evaluate(kind=>{
  375 |   const frame=document.getElementById('player') as HTMLIFrameElement;
  376 |   (window as any).__suggestionReplies=[];
  377 |   window.addEventListener('message',event=>{
```