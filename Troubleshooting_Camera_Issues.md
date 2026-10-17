# Troubleshooting Camera Issues

A step-by-step guide to locating an IP camera, configuring its video streams, identifying RTSP URLs, and checking the streams in go2rtc and a browser.

## 1. Find the camera's IP address

- **Windows:** Use [Advanced IP Scanner](https://www.advanced-ip-scanner.com/) to find the camera on your network.

## 2. Sign in to the camera

1. Open the camera's IP address in a browser and try to sign in.
2. If a password is required, check the camera's app for the username or consult its documentation.
3. For many **Zintronic** cameras, the initial credentials may be:
   - **Username:** `admin`
   - **Password:** `12345` or `123456`

## 3. Configure the video streams

In the camera's **Video** settings:

1. Set the codec to **H.264 (Main Profile)** for both the main stream and the substream.
2. Keep the **main stream** (high resolution) at its default resolution.
3. Reduce the **substream** (low resolution) to approximately **640 × 360**, if supported.

## 4. Find the main and substream RTSP URLs

If you cannot identify working RTSP URLs, use **ONVIF Device Manager (ODM)**:

1. Get ODM from [SourceForge](https://sourceforge.net/projects/onvifdm/).
2. Connect to the camera in ODM.
3. Select a stream under **Profiles**.
4. Open the **Live Video** tab to inspect the stream's URL.
5. Repeat for both the high-resolution main stream and the low-resolution substream.

## 5. Troubleshoot the streams in go2rtc

1. Start go2rtc with your camera configuration:

   ```bash
   go2rtc -c cameras.yaml
   ```

   Here, `cameras.yaml` is the configuration file containing your camera's RTSP feed definitions.

2. Open the go2rtc interface in a browser:

   ```text
   http://localhost:1984
   ```

3. Select the relevant stream. Inspect its **Info** and available **Links**.
4. Try the available stream links to determine which ones work.

## 6. Check the actual displayed video resolution

In **Microsoft Edge**, open **Developer Tools → Console** (typically with `F12`). Run the appropriate JavaScript expression for the view you have open.

### Camera wall view

With the camera wall visible, run:

```javascript
tiles.map(t => {
  const v = t.el.querySelector("video");
  return `${t.cam.name}: ${v.videoWidth}x${v.videoHeight}`;
});
```

This returns each camera's name and displayed video dimensions.

### Single-camera view

With a single camera viewer open, run:

```javascript
`${viewer.querySelector("video").videoWidth}x${viewer.querySelector("video").videoHeight}`
```

This returns the video dimensions for the selected camera.

> **Scope:** These Console expressions depend on the camera wall/viewer application exposing the `tiles` and `viewer` variables, as in the original notes. They are not general-purpose Edge commands for every website.
