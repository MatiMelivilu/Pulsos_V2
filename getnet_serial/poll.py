from getnet_serial import POS

with POS("/dev/ttyACM0") as pos:
    print(pos.poll())
