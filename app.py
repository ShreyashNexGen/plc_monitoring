from flask import Flask, render_template, jsonify, request, send_file
import sqlite3
import pandas as pd
import threading
import time
from opcua import Client,ua
from datetime import datetime
import os
app = Flask(__name__)

# 🔴 Connect to OPC UA Server
OPC_SERVER_URL = "opc.tcp://192.168.0.1:4840"
client = Client(OPC_SERVER_URL)
client.connect()
def init_db():
    conn = sqlite3.connect("data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datetime TEXT,
            speed REAL,
            length REAL,
            pieces INTEGER
        )
    """)
    conn.commit()
    conn.close()
init_db()
# 🔴 PLC Tag Addresses
# //ns=3;s="Data_block_1"."Sample_DB"."Date"
TAGS = {
    "datetime": 'ns=3;s="Data_block_1"."Sample_DB"."Date"',
    "length": 'ns=3;s="Data_block_1"."Sample_DB"."Length"',
    "pieces": 'ns=3;s="Data_block_1"."Sample_DB"."Pieces"',
    "speed": 'ns=3;s="Data_block_1"."Sample_DB"."Speed"',
    "logData": 'ns=3;s="Data_block_1"."Sample_DB"."LogData"'
}

# 🔴 Data Buffer for Live Chart
from collections import OrderedDict

BUFFER_SIZE = 1000
data_buffer = OrderedDict()
last_sent_index = 0  # Track last sent position

def read_plc_data():
    print("reached here")
    global data_buffer
    while True:
        log_data = client.get_node(TAGS["logData"]).get_value()

        if log_data == 1 or log_data == 0:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            speed = client.get_node(TAGS["speed"]).get_value()
            length = client.get_node(TAGS["length"]).get_value()
            pieces = client.get_node(TAGS["pieces"]).get_value()

            # 🔵 Store in Buffer (only unique timestamps)
            if timestamp not in data_buffer:
                if len(data_buffer) >= BUFFER_SIZE:
                    data_buffer.pop(next(iter(data_buffer)))  # Remove oldest entry
                data_buffer[timestamp] = {"datetime": timestamp, "speed": speed, "length": length, "piecesCounter": pieces}

            # print("Updated buffer:", list(data_buffer.values()))  # Debugging

            # 🔵 Save to SQLite
            conn = sqlite3.connect("data.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO data (datetime, speed, length, pieces) VALUES (?, ?, ?, ?)",
                           (timestamp, speed, length, pieces))
            conn.commit()
            conn.close()

            # 🔵 Reset LogData
            client.get_node(TAGS["logData"]).set_value(ua.DataValue(ua.Variant(0, ua.VariantType.Float)))

        time.sleep(1)

# 🔴 Serve Only New Data
@app.route("/api/live-data")
def live_data():
    global data_buffer, last_sent_index
    
    buffer_list = list(data_buffer.values())  # Convert OrderedDict to list
    if last_sent_index < len(buffer_list):
        new_data = buffer_list[last_sent_index:]  # Get only new data
        last_sent_index = len(buffer_list)  # Update last sent index
        print("Serving new data:", new_data)  # Debugging
        return jsonify(new_data)
    
    return jsonify([])  # No new data to send



# 🔴 Serve Overview Data with Pagination
@app.route("/api/data")
def get_paginated_data():
    page = int(request.args.get("page", 1))
    per_page = 10

    conn = sqlite3.connect("data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM data")
    total_records = cursor.fetchone()[0]

    cursor.execute("SELECT * FROM data ORDER BY datetime DESC LIMIT ? OFFSET ?", (per_page, (page - 1) * per_page))
    rows = cursor.fetchall()
    conn.close()

    data = [{"datetime": row[0], "speed": row[1], "length": row[2], "pieces": row[3]} for row in rows]

    return jsonify({"data": data, "total_pages": (total_records // per_page) + 1})

# 🔴 Summary Stats
@app.route("/api/summary")
def get_summary():
    conn = sqlite3.connect("data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT AVG(speed), SUM(length), SUM(pieces) FROM data")
    avg_speed, total_length, total_pieces = cursor.fetchone()
    conn.close()
    return jsonify({
        "avg_speed": round(avg_speed, 2) if avg_speed else 0,
        "total_length": round(total_length, 2) if total_length else 0,
        "total_pieces": int(total_pieces) if total_pieces else 0
    })

# 🔴 Excel Download
@app.route("/download")
def download_data():
    conn = sqlite3.connect("data.db")
    df = pd.read_sql("SELECT * FROM data", conn)
    conn.close()
    df.to_excel("data.xlsx", index=False)
    return send_file("data.xlsx", as_attachment=True)

# 🔴 Serve Pages
@app.route("/")
def visualization():
    return render_template("visualization.html")

@app.route("/overview")
def overview():
    return render_template("overview.html")

if __name__ == "__main__":
    
   if __name__ == "__main__":
    threading.Thread(target=read_plc_data, daemon=True).start()
    app.run(debug=True, use_reloader=False)
