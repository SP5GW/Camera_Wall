To find you camera ip address use:
    windows: Advanced IP Scanner app
    rbpi:

Try to log on to the camera, if it is password protected try to retreive at least user name from the app, or consult documentation. Many Zintronic cameras have user name set to admin and initial password set to 12345 or 123456.

Inside the camera typically in video section select H.264/main profile codecs for both main and sub channel. Lower the resolution of subchannel to something like 6420x360 for the sub channel. Leave resolution of the main channel at default value.

If you have issues with finding working rstp url for the main (hi res) and sub (low res) streams, which your camera transmits use ODM app (sourceforge.net/projects/onvifdm). There you can select video stream (Menu Profiles) and then in Live Video tab you will be able to see actual url.

To troubleshoot go2rtc backend especially to find out rtp url lauch go2rtc:
go2rtc -c cameras.yaml (where cameras.yaml is config file specifying rstp feeds)

from the browser select localhost:1984

there you need to click on info stream and links and try several of them to find working one.

You can test what is being streamed from the camera by using Edge and selecting More Tools -> Programmer Tools -> Console and typing:

with wall open:
tiles.map(t => { const v = t.el.querySelector("video"); return `${t.cam.name}: ${v.videoWidth}x${v.videoHeight}`; })

with single camera view open:
`${viewer.querySelector("video").videoWidth}x${viewer.querySelector("video").videoHeight}`

