# mock_streamer.py
import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# Initialize the GStreamer framework
Gst.init(None)

# Define the pipeline using high-index virtual device node /dev/video200
# videotestsrc: generates standardized SMPTE color bars
# timeoverlay: superimposes a high-precision clock to measure switching latency
# videoconvert: transforms raw frame data into standard YUY2 format for V4L2 ingestion
# v4l2sink: pipes the processed data straight into our forced loopback block device
pipeline_str = (
    "videotestsrc pattern=colors ! "
    "video/x-raw,width=640,height=480,framerate=30/1 ! "
    "timeoverlay halignment=left valignment=bottom text=\"MOCK_CAM_200: \" shaded-background=true ! "
    "videoconvert ! "
    "video/x-raw,format=YUY2 ! "
    "v4l2sink device=/dev/video200"
)

try:
    # Instantiate and start the execution pipeline
    pipeline = Gst.parse_launch(pipeline_str)
    pipeline.set_state(Gst.State.PLAYING)
    print("=======================================================")
    print("🎥 Vid_Mux_TEST: Synthetic stream active on /dev/video200")
    print("=======================================================")
    
    # Run the GLib Main Loop to maintain asynchronous video processing
    loop = GLib.MainLoop()
    loop.run()

except KeyboardInterrupt:
    print("\n🛑 Shutting down synthetic stream generator...")
    pipeline.set_state(Gst.State.NULL)
except Exception as e:
    print(f"❌ Critical pipeline error: {str(e)}", file=sys.stderr)
    sys.exit(1)
