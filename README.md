Project Overview: Web-based camera for powder bed monitoring and RPI configuration (in cooperation with Kurtz Ersa)

Software Requirements: Docker, Python3
Dockerfile Build Instructions: "docker build -t <image-name> ."
Docker Run instructions: "sudo docker run -it --privileged -v /dev/:/dev/ -v /run/udev:/run/udev -v /run/dbus/:/run/dbus -p 8000:8000 <image-name>"
