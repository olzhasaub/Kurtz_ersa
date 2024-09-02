import os, io, logging, json, time, re
from datetime import datetime
from threading import Condition

from flask import Flask, render_template, request, jsonify, Response, send_file, abort

from PIL import Image
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput
from libcamera import Transform, controls
import cv2

app = Flask(__name__) 

#create camera object and initialize
picam2 = Picamera2()
recording = False

# Int Picamera2 and default settings
timelapse_running = False
timelapse_thread = None

current_dir = os.path.dirname(os.path.abspath(__file__))
camera_config_path = os.path.join(current_dir, 'camera-config.json')
with open(camera_config_path, "r") as file:
    camera_config = json.load(file)
print(f'\nCamera Config:\n{camera_config}\n')

camera_module_info_path = os.path.join(current_dir, 'camera-module-info.json')
with open(camera_module_info_path, "r") as file:
    camera_module_info = json.load(file)
camera_properties = picam2.camera_properties

live_settings = camera_config.get('controls', {})
rotation_settings = camera_config.get('rotation', {})
sensor_mode = camera_config.get('sensor-mode', 1)
capture_settings = camera_config.get('capture-settings', {}) 

selected_resolution = capture_settings["Resolution"]
resolution = capture_settings["available-resolutions"][selected_resolution]
print(f'\nCamera Settings:\n{capture_settings}\n')
print(f'\nCamera Set Resolution:\n{resolution}\n')

camera_modes = picam2.sensor_modes
mode = picam2.sensor_modes[sensor_mode]

video_config = picam2.create_video_configuration(main={'size':resolution}, sensor={'output_size': mode['size'], 'bit_depth': mode['bit_depth']})

default_settings = picam2.camera_controls
live_settings = {key: value for key, value in live_settings.items() if key in default_settings}

UPLOAD_FOLDER = os.path.join(current_dir, 'static/gallery')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)



def start_camera_stream():
    global picam2, output, video_config
    picam2.configure(video_config)
    output = StreamingOutput()
    picam2.start_recording(JpegEncoder(), FileOutput(output))
    metadata = picam2.capture_metadata()
    time.sleep(1)

def configure_camera(live_settings):
    picam2.set_controls(live_settings)
    time.sleep(0.5)

def take_photo():
    global picam2, capture_settings
    try:
        timestamp = int(datetime.timestamp(datetime.now()))
        image_name = f'pimage_{timestamp}'
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
        request = picam2.capture_request()
        request.save("main", f'{filepath}.jpg')
        request.release()
        logging.info(f"Image captured successfully. Path: {filepath}")
    except Exception as e:
        logging.error(f"Error capturing image: {e}")


def stop_camera_stream():
    global picam2
    picam2.stop_recording()
    time.sleep(1)

def save_sensor_mode(sensor_mode):
    try:
        with open('camera-config.json', 'r') as file:
            camera_config = json.load(file)

        camera_config['sensor-mode'] = sensor_mode  
        
        with open('camera-config.json', 'w') as file:
            json.dump(camera_config, file, indent=4)

        return jsonify(success=True, message="Settings saved successfully")
    except Exception as e:
        logging.error(f"Error in saving data: {e}")
        return jsonify(success=False, message=str(e))

def generate_frames():
    global output
    while True:
        with output.condition:
            output.condition.wait()
            frame = output.frame
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        
def load_settings(settings_file):
    try:
        with open(settings_file, 'r') as file:
            settings = json.load(file)
            print(settings)
            return settings
    except FileNotFoundError:
        logging.error(f"Settings file {settings_file} not found")
        return None
    except Exception as e:
        logging.error(f"Error loading camera settings: {e}")
        return None

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()



@app.route("/")
def start():
    return render_template("start.html")

@app.route("/home")
def home():
    return render_template("home.html", title="Camera Stream", live_settings=live_settings, rotation_settings=rotation_settings, settings_from_camera=default_settings, capture_settings=capture_settings)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/capture_photo', methods=['POST'])
def capture_photo():
    try:
        take_photo() 
        time.sleep(1)
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/get_photo/<resolution>/<exposure>/<format>', methods=['GET'])
def get_photo(resolution, exposure, format):
    try:
        width, height = map(int, resolution.split('x'))
    except ValueError:
        return abort(400, description="Invalid resolution format. Use <width>x<height>.")
    
    try:
        exposure = int(exposure)
    except ValueError:
        return abort(400, description="Invalid exposure value.")
    
    if format not in ['jpeg', 'png']:
        return abort(400, description="Invalid format. Use 'jpeg' or 'png'.")
    
    stop_camera_stream()

    timestamp = int(datetime.timestamp(datetime.now()))
    image_name = f'pimage_{timestamp}.{format}'
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], image_name)

    capture_configs = picam2.create_still_configuration(main={"size": (width, height)})
    picam2.configure(capture_configs)
    picam2.set_controls({"ExposureTime": exposure})

    request = picam2.capture_request()
    request.save("main", filepath)
    request.release()

    

    stop_camera_stream()
    start_camera_stream()

    mimetype = 'image/jpeg' if format == 'jpeg' else 'image/png'

    return send_file(filepath, mimetype, as_attachment=True, download_name=image_name)


@app.route('/image_gallery')
def image_gallery():
    try:
        image_files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.jpg')]
        print(image_files)

        if not image_files:
            return render_template('no_files.html')

        files_and_timestamps = []
        for image_file in image_files:
            unix_timestamp = int(image_file.split('_')[-1].split('.')[0])
            timestamp = datetime.utcfromtimestamp(unix_timestamp).strftime('%Y-%m-%d %H:%M:%S')

            dng_file = os.path.splitext(image_file)[0] + '.dng'
            has_dng = os.path.exists(os.path.join(UPLOAD_FOLDER, dng_file))

            files_and_timestamps.append({'filename': image_file, 'timestamp': timestamp, 'has_dng': has_dng, 'dng_file': dng_file})

        files_and_timestamps.sort(key=lambda x: x['timestamp'], reverse=True)

        page = request.args.get('page', 1, type=int)
        items_per_page = 15
        total_pages = (len(files_and_timestamps) + items_per_page - 1) // items_per_page

        start_page = max(1, page - 1)
        end_page = min(page + 3, total_pages)
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        files_and_timestamps_page = files_and_timestamps[start_index:end_index]

        return render_template('image_gallery.html', image_files=files_and_timestamps_page, page=page, start_page=start_page, end_page=end_page)
    except Exception as e:
        logging.error(f"Error loading image gallery: {e}")
        return render_template('error.html', error=str(e))

@app.route('/detect_edges', methods=['POST']) 
def detect_edges():
    global picam2, capture_settings
    try:
        timestamp = int(datetime.timestamp(datetime.now()))  #current time
        image_name = f'pimage_{timestamp}'      #image name
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'{image_name}.jpg')  #specifying the folder in which the image will be saved
        request = picam2.capture_request()   #
        request.save("main", filepath)
        request.release()

        edges_name = f'pimage_edges_{timestamp}.jpg'

        if not os.path.exists(filepath):
            return jsonify({"success": False, "error": "File not found"}), 404
        
        edge_filepath = os.path.join(app.config['UPLOAD_FOLDER'], edges_name)
        
        image = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)   #edge detection
        edges = cv2.Canny(image, threshold1=100, threshold2=200, apertureSize=7)
        cv2.imwrite(edge_filepath, edges)

    
        return jsonify({"success": True, "edge_image": filepath}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/delete_image/<filename>', methods=['DELETE'])
def delete_image(filename):
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        os.remove(filepath)
        return jsonify(success=True, message="Image deleted successfully")
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/view_image/<filename>', methods=['GET'])
def view_image(filename):
    return render_template('view_image.html', filename=filename)

@app.route('/download_image/<filename>', methods=['GET'])
def download_image(filename):
    try:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        return send_file(image_path, as_attachment=True)
    except Exception as e:
        print(f"Error downloading image: {e}")
        abort(500)

@app.route("/camera_info")
def camera_info():
    connected_camera = picam2.camera_properties['Model']
    connected_camera_data = next((module for module in camera_module_info["camera_modules"] if module["sensor_model"] == connected_camera), None)
    if connected_camera_data:
        return render_template("camera_info.html", title="Camera Info", connected_camera_data=connected_camera_data, camera_modes=camera_modes, sensor_mode=sensor_mode)
    else:
        return jsonify(error="Camera module data not found")
    
@app.route('/update_live_settings', methods=['POST'])
def update_settings():
    global live_settings, capture_settings, picam2, video_config, resolution, sensor_mode, mode
    try:
        data = request.get_json()
        print(data)
        for key in data:
            if key in capture_settings:
                if key in ('Resolution'):
                    capture_settings['Resolution'] = int(data[key])
                    selected_resolution = int(data[key])
                    resolution = capture_settings["available-resolutions"][selected_resolution]
                    stop_camera_stream()
                    video_config = picam2.create_video_configuration(main={'size':resolution}, sensor={'output_size': mode['size'], 'bit_depth': mode['bit_depth']})
                    start_camera_stream()
                    return jsonify(success=True, message="Settings updated successfully", settings=capture_settings)
                elif key in ('makeRaw'):
                    capture_settings[key] = data[key]
                    return jsonify(success=True, message="Settings updated successfully", settings=capture_settings)
            elif key == ('sensor_mode'):
                sensor_mode = int(data[key])
                mode = picam2.sensor_modes[sensor_mode]
                stop_camera_stream()
                video_config = picam2.create_video_configuration(main={'size':resolution}, sensor={'output_size': mode['size'], 'bit_depth': mode['bit_depth']})
                start_camera_stream()
                save_sensor_mode(sensor_mode)
                return jsonify(success=True, message="Settings updated successfully", settings=sensor_mode)
    except Exception as e:
        return jsonify(success=False, message=str(e))
    
if __name__ == "__main__":

    start_camera_stream()

    configure_camera(live_settings)

    app.run(debug=False, host='0.0.0.0', port=8000)
