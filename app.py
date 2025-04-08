from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for, session, flash
import sqlite3
import pandas as pd
import threading
import time
from opcua import Client,ua
from datetime import datetime
import os
from werkzeug.security import generate_password_hash, check_password_hash
app = Flask(__name__)
app.secret_key = "supersecretkey"  # Change this to a strong secret key

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
    # Table for User Authentication
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
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

        time.sleep(5)

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

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        hashed_password = generate_password_hash(password)

        conn = sqlite3.connect("data.db")
        cursor = conn.cursor()

        try:
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
            conn.commit()
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists. Choose a different one.", "danger")
        finally:
            conn.close()

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect("data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            session["user"] = username
            flash("Login successful!", "success")
            return redirect(url_for("overview"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")
@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# 🔴 Middleware to Protect Routes
def login_required(route_function):
    def wrapper(*args, **kwargs):
        if "user" not in session:
            flash("You need to log in first.", "warning")
            return redirect(url_for("login"))
        return route_function(*args, **kwargs)
    wrapper.__name__ = route_function.__name__
    return wrapper
# 🔴 Serve Pages
@app.route("/")
@login_required
def visualization():
    return render_template("visualization.html")
@app.route("/overview")
@login_required
def overview():
    return render_template("overview.html")

@app.route("/api/data")
def get_paginated_data():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 10))  # Default 10, can be changed
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    conn = sqlite3.connect("data.db")
    cursor = conn.cursor()

    query = "SELECT COUNT(*) FROM data"
    filters = []
    
    if start_date and end_date:
        query += " WHERE datetime BETWEEN ? AND ?"
        filters.extend([start_date, end_date])

    cursor.execute(query, tuple(filters) if filters else ())
    total_records = cursor.fetchone()[0]

    total_pages = (total_records + per_page - 1) // per_page  # Proper rounding up

    data_query = "SELECT * FROM data"
    if start_date and end_date:
        data_query += " WHERE datetime BETWEEN ? AND ?"
    
    data_query += " ORDER BY datetime DESC LIMIT ? OFFSET ?"

    cursor.execute(data_query, tuple(filters + [per_page, (page - 1) * per_page]) if filters else (per_page, (page - 1) * per_page))
    rows = cursor.fetchall()
    conn.close()

    data = [{"datetime": row[2], "speed": row[1], "length": row[3], "pieces": row[4]} for row in rows]

    return jsonify({
        "data": data,
        "total_pages": total_pages,
        "current_page": page,
        "per_page": per_page,
        "total_records": total_records
    })



# @app.route("/api/chart-data")
# def get_chart_data():
#     start = request.args.get("start")
#     end = request.args.get("end")

#     query = "SELECT length, SUM(pieces) FROM data"
#     params = []

#     if start and end:
#         query += " WHERE datetime BETWEEN ? AND ?"
#         params = [start, end]

#     query += " GROUP BY length ORDER BY length"

#     conn = sqlite3.connect("data.db")
#     cursor = conn.cursor()
#     cursor.execute(query, params)
#     rows = cursor.fetchall()
#     conn.close()

#     data = [{"length": row[0], "pieces": row[1]} for row in rows]
#     return jsonify(data)
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


# @app.route("/overview")
# def overview():
#     return render_template("overview.html")
# def fetch_report_data():
#     """Fetch production report data from the database"""
#     conn = sqlite3.connect("data.db")
#     cursor = conn.cursor()
#     query = """
#     SELECT Thickness, Outer_Diameter, Length, Speed, Date_Time, Breakdown,
#            Row_Change, Maintenance, Pieces, Roof_Cuts, Wastage, Type_of_Tube
#     FROM Production_Report
#     """
    
#     cursor.execute(query)
#     data = cursor.fetchone()  # Fetch the first row (adjust if needed)

#     conn.close()

#     if data:
#         report_data = {
#             "thickness": data[0],
#             "outer_diameter": data[1],
#             "length": data[2],
#             "speed": data[3],
#             "date_time": data[4],
#             "breakdown": data[5],
#             "row_change": data[6],
#             "maintenance": data[7],
#             "pieces": data[8],
#             "roof_cuts": data[9],
#             "wastage": data[10],
#             "type_of_tube": data[11]
#         }
#         return report_data
#     return None
# @app.route('/report')
# @login_required
# def report_page():
#     """Render the report page with dynamic data"""
#     report_data = fetch_report_data()
#     return render_template('report.html', report=report_data)
@app.route("/report")
@login_required
def report():
    return render_template("report1.html")

# @app.route('/download-report')
# def download_report():
    """Generate and download the report as a CSV file"""
    report_data = fetch_report_data()

    if not report_data:
        return "No data available", 404

    df = pd.DataFrame([report_data])
    file_path = "report.csv"
    df.to_csv(file_path, index=False)

    return send_file(file_path, as_attachment=True)
if __name__ == "__main__":

   if __name__ == "__main__":
    threading.Thread(target=read_plc_data, daemon=True).start()
    app.run(debug=True, use_reloader=False)
