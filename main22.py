import csv
import math
import os
import re
import struct
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# ==========================================
# CONSTANTS & CONFIGURATIONS
# ==========================================
# ขนาด fixed-length สำหรับฟิลด์
# Struct Format: < (Little-Endian)
# i  : comp_id (int4)
# 16s: pc_name (char[16])
# 16s: zone (char[16])
# i  : ram_gb (int4)
# f  : rate_per_hr (float4)
# 16s: status (char[16])
# ?  : is_active (bool1)
# 20s: use_start (เวลาเริ่มใช้งาน "YYYY-MM-DD HH:MM:SS" ว่าง = ไม่ได้ใช้งาน)
LEGACY_RECORD_FORMAT = "<i16s16sif16s?"
LEGACY_RECORD_SIZE = struct.calcsize(LEGACY_RECORD_FORMAT)
PREVIOUS_RECORD_FORMAT = "<i16s16sif16s?20s"   # รุ่นก่อน (ไม่มี VIP Member ID)
PREVIOUS_RECORD_SIZE = struct.calcsize(PREVIOUS_RECORD_FORMAT)
RECORD_FORMAT = "<i16s16sif16s?20si"          # + vip_user_id (เชื่อมกับ member_applicants / customers.bin)
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SHOW_TIME_FORMAT = "%Y-%m-%d %H:%M"
LEGACY_MEMBER_APPLICATION_FORMAT = "<i32s16s20s16s"
LEGACY_MEMBER_APPLICATION_SIZE = struct.calcsize(LEGACY_MEMBER_APPLICATION_FORMAT)
MEMBER_APPLICATION_FORMAT = "<i32s16s24s20s?"
MEMBER_APPLICATION_SIZE = struct.calcsize(MEMBER_APPLICATION_FORMAT)
LEGACY_BOOKING_FORMAT = "<iiif20s?"
LEGACY_BOOKING_SIZE = struct.calcsize(LEGACY_BOOKING_FORMAT)
PREVIOUS_BOOKING_FORMAT = "<iiiiff24s20s?"
PREVIOUS_BOOKING_SIZE = struct.calcsize(PREVIOUS_BOOKING_FORMAT)
BOOKING_V2_FORMAT = "<iiiiff24s20s10s24s?"          # รุ่นก่อน (ยังไม่มี PC Name)
BOOKING_V2_SIZE = struct.calcsize(BOOKING_V2_FORMAT)
BOOKING_FORMAT = "<iiiiff24s20s10s24s?16s"          # + pc_name (ชื่อเครื่องที่กรอกตอนจอง)
BOOKING_SIZE = struct.calcsize(BOOKING_FORMAT)
RATE_PER_HOUR = 20.0
TOTAL_PCS = 50  # จำนวน PC ขั้นต่ำที่แสดงในตารางสถานะ (ถ้าเพิ่ม PC-51, PC-52, ... ตารางจะขยายเองอัตโนมัติ)
BOOKING_SLOTS = {
    "1": ("13:00-16:00", 3),
    "2": ("16:00-20:00", 4),
}
VIP_DISCOUNTS = {
    "VIP ระดับ 1": 10.0,
    "VIP ระดับ 2": 13.0,
    "VIP ระดับ 3": 20.0,
}

# กำหนดไฟล์ไบนารี 3 ไฟล์ และไฟล์รายงาน 1 ไฟล์
FILE_COMPUTERS = "computers.bin"
FILE_CUSTOMERS = "customers.bin"
FILE_BOOKINGS = "bookings.bin"
FILE_REPORT = "summary_report.txt"
PC_STATUS_TITLE_PREFIX = "สถานะเครื่องคอมพิวเตอร์ทั้งหมด"
FILE_MEMBER_REPORT = "member_applicants_report.txt"
FILE_ISSUES = "problem_reports.csv"
FILE_ISSUE_REPORT = "problem_reports.txt"
ISSUE_FIELDS = (
    "report_id", "comp_id", "pc_name", "issue_date", "issue_type",
    "description", "reported_at", "status",
)
CUSTOM_ISSUE = "อื่นๆ"
ISSUE_TYPES = {
    "1": "RAM พัง",
    "2": "จอเป็นเส้น",
    "3": "จอพัง",
    "4": CUSTOM_ISSUE,  # ลูกค้ากรอกเอง
}

# หน่วยความจำชั่วคราวสำหรับเก็บประวัติการทำงานล่าสุดในเซสชัน (Audit Log)
ACTION_LOGS = []

# ==========================================
# TABLE HELPERS (จัดตารางให้ตรงแม้มีภาษาไทย)
# ==========================================
def display_width(text) -> int:
    """ความกว้างที่แสดงจริงบน terminal: สระบน/ล่างและวรรณยุกต์ไทยกว้าง 0 ช่อง"""
    width = 0
    for ch in str(text):
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text, width: int) -> str:
    """เติมช่องว่างท้ายข้อความตามความกว้างที่แสดงจริง (แทน f"{x:<N}")"""
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def clip(text, width: int) -> str:
    """ตัดข้อความให้ไม่เกินความกว้างที่แสดงจริง"""
    out, used = "", 0
    for ch in str(text):
        w = display_width(ch)
        if used + w > width:
            break
        out += ch
        used += w
    return out


def format_table(headers, rows, min_widths=None, row_separators=False, bottom_border=False) -> list[str]:
    """สร้างตารางข้อความ โดยคำนวณความกว้างคอลัมน์จากข้อมูลจริง เส้นจึงตรงเสมอ"""
    # แทน \n \r \t ในข้อมูลด้วยช่องว่าง กันเส้นตารางขาด/เบี้ยว
    rows = [[" ".join(str(cell).split("\n")).replace("\r", " ").replace("\t", " ") for cell in row] for row in rows]
    min_widths = min_widths or [0] * len(headers)
    widths = [
        max([min_widths[i], display_width(header)] + [display_width(row[i]) for row in rows])
        for i, header in enumerate(headers)
    ]
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def fmt(cells):
        return "| " + " | ".join(pad(cell, w) for cell, w in zip(cells, widths)) + " |"

    lines = [border, fmt(headers), border]
    for row in rows:
        lines.append(fmt(row))
        if row_separators:
            lines.append(border)
    if bottom_border and not row_separators:
        lines.append(border)  # เส้นปิดท้ายตาราง
    return lines


def log_action(action: str):
    """บันทึกประวัติการทำงานพร้อมประทับเวลา"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ACTION_LOGS.append(f"[{now_str}] {action}")


def parse_time(text: str):
    """แปลงข้อความเวลาเป็น datetime (คืน None ถ้าว่าง/อ่านไม่ได้)"""
    try:
        return datetime.strptime(str(text).strip()[:19], TIME_FORMAT)
    except ValueError:
        return None


def show_time(value) -> str:
    """แสดงเวลาในตาราง (ว่าง = '-')"""
    return value.strftime(SHOW_TIME_FORMAT) if value else "-"


COMPUTER_STATUSES = ("Available", "In Use", "Maint")


def normalize_status(text: str) -> str:
    """แปลงสถานะที่พิมพ์ (in use / IN USE / maint ...) ให้เป็นรูปแบบมาตรฐาน Available / In Use / Maint
    ถ้าไม่รู้จักจะคืนค่าเดิม (ตัดช่องว่าง)"""
    key = " ".join(str(text).replace("_", " ").split()).casefold()
    aliases = {
        "available": "Available", "avail": "Available",
        "in use": "In Use", "inuse": "In Use", "using": "In Use",
        "maint": "Maint", "maintenance": "Maint",
    }
    return aliases.get(key, str(text).strip())


def is_vip_zone(zone: str) -> bool:
    return normalize_computer_zone(zone).upper().startswith("VIP")


def vip_members() -> list[dict]:
    """สมาชิก VIP ที่ใช้งานอยู่ทั้งหมด (ดึงจาก customers.bin = member_applicants_report)"""
    return sorted(
        (m for m in read_member_applications() if m["is_active"] and member_discount(m["member_level"]) > 0),
        key=lambda m: m["user_id"],
    )


def computer_vip_cost(record: dict, members_by_id: dict):
    """(ป้าย VIP Member, ส่วนลด %, ราคาสุทธิ THB) ของเครื่อง คำนวณจากระดับสมาชิกในไฟล์สมาชิก
    ราคาสุทธิ = ราคาแพ็กเกจ (Rate) x (1 - ส่วนลด/100)"""
    member = members_by_id.get(record.get("vip_user_id", 0)) if is_vip_zone(record["zone"]) else None
    discount = member_discount(member["member_level"]) if member else 0.0
    label = f"{member['user_id']} {clip(member['name'], 14)}" if member else "-"
    return label, discount, record["rate_per_hr"] * (1 - discount / 100)


def vip_members_table_lines(records: list[dict] | None = None) -> list[str]:
    """ตารางรายชื่อสมาชิก VIP ทั้งหมด + เครื่องที่ผูกกับสมาชิกคนนั้น"""
    members = vip_members()
    if not members:
        return ["- ยังไม่มีสมาชิก VIP (สมัครได้ที่เมนู 3) / No VIP members yet"]
    if records is None:
        records = read_all_computers()
    all_bookings = read_all_bookings()
    rows = []
    for member in members:
        pcs = ", ".join(
            f"{r['pc_name']} ({r['comp_id']})" for r in records
            if r["is_active"] and r["vip_user_id"] == member["user_id"] and is_vip_zone(r["zone"]))
        rows.append([
            member["user_id"], member["name"],
            MEMBER_LEVEL_EN.get(member["member_level"], member["member_level"]),
            f"{member_discount(member['member_level']):g}%", pcs or "-",
            sum(1 for b in all_bookings if b["is_active"] and b["user_id"] == member["user_id"]),
        ])
    return format_table(["User ID", "Full Name", "Member Level", "Discount", "Linked PC (CompID)", "Bookings"],
                        rows, min_widths=[8, 20, 14, 8, 20, 8], bottom_border=True)


def booking_state(booking: dict, now: datetime) -> str:
    start, end = booking_times(booking)
    if not start or not end:
        return "-"
    if now < start:
        return "Upcoming"
    return "In Progress" if now < end else "Finished"


def build_booking_table_lines(records: list[dict] | None = None, bookings: list[dict] | None = None) -> list[str]:
    """ตารางการจอง (จาก bookings.bin) เชื่อม CompID -> computers.bin (PC Name, Member) และ User ID -> customers.bin"""
    if bookings is None:
        bookings = read_all_bookings()
    bookings = [b for b in bookings if b["is_active"]]
    if not bookings:
        return ["- ยังไม่มีรายการจอง / No bookings yet"]
    if records is None:
        records = read_all_computers()
    pcs = {r["comp_id"]: r for r in records}
    members = {m["user_id"]: m for m in read_member_applications() if m["is_active"]}
    now = datetime.now()
    rows = []
    for b in sorted(bookings, key=lambda x: (x["booking_date"], x["time_slot"], x["booking_id"])):
        rec = pcs.get(b["comp_id"])
        member = members.get(b["user_id"])
        rows.append([
            b["booking_id"], b["comp_id"],
            b.get("pc_name") or (rec["pc_name"] if rec else "(deleted)"), rec["zone"] if rec else "-",
            MEMBER_LEVEL_EN.get(b["member_level"], b["member_level"]),
            f"{member['user_id']} {clip(member['name'], 14)}" if member else "-",
            b["booking_date"], b["time_slot"], f"{b['hours']:g}", f"{b['total']:.2f}",
            b["booked_at"], booking_state(b, now),
        ])
    return format_table(
        ["BookID", "CompID", "PC Name", "Member", "Member Level", "VIP Member", "Date", "Time",
         "Hours", "Total (THB)", "Booked At", "State"],
        rows, min_widths=[6, 6, 10, 10, 12, 18, 10, 11, 5, 11, 19, 11], bottom_border=True)


def ask_vip_member(current_id: int = 0) -> int:
    """ถาม Member ID + ชื่อ VIP แล้วตรวจกับไฟล์สมาชิก (ต้องเป็น VIP ที่ใช้งานอยู่ และ ID กับชื่อต้องตรงกัน)
    current_id > 0 = มีสมาชิกผูกอยู่แล้ว กด Enter เพื่อคงเดิม / 0 = ยกเลิกการผูก"""
    members = {m["user_id"]: m for m in vip_members()}
    if not members:
        print("[!] ยังไม่มีสมาชิก VIP ที่ใช้งานอยู่ (สมัครสมาชิกที่เมนู 3 ก่อน) บันทึกเครื่องโดยยังไม่ผูกสมาชิก")
        return 0
    print("\nรายชื่อสมาชิก VIP ทั้งหมด:")
    print("\n".join(vip_members_table_lines()))
    keep = members.get(current_id)
    while True:
        hint = f" [{keep['user_id']} {keep['name']}; Enter = คงเดิม]" if keep else ""
        text = input(f"Enter Member ID (VIP Member){hint} (0 = ไม่ผูกสมาชิก): ").strip()
        if not text and keep:
            return keep["user_id"]
        if text == "0":
            return 0
        if not text.isdigit() or int(text) not in members:
            print("[!] ไม่พบ Member ID นี้ในรายชื่อ VIP ที่ใช้งานอยู่ กรุณากรอกใหม่")
            continue
        member = members[int(text)]
        name = input("Enter Name VIP (VIP Name) : ").strip()
        if name.casefold() != member["name"].strip().casefold():
            print("[!] ชื่อไม่ตรงกับ Member ID นี้ กรุณากรอกใหม่")
            continue
        return member["user_id"]


def computer_use_times(record: dict):
    """เวลาเริ่ม/สิ้นสุดการใช้งานของเครื่อง: สิ้นสุด = เริ่ม + (เรต / 20 บาทต่อชั่วโมง)
    ใช้ได้เมื่อสถานะ In Use เท่านั้น เมื่อเพิ่มเรตราคา เวลาสิ้นสุดจะเพิ่มตามอัตโนมัติ"""
    start = parse_time(record.get("use_start", ""))
    if record["status"] != "In Use" or start is None:
        return None, None
    hours = record["rate_per_hr"] / RATE_PER_HOUR if RATE_PER_HOUR else 0
    return start, start + timedelta(hours=hours)


def parse_slot_minutes(time_slot: str):
    """'HH:MM-HH:MM' -> (นาทีเริ่ม, นาทีสิ้นสุด) หรือ None ถ้ารูปแบบไม่ถูกต้อง"""
    try:
        start_text, end_text = str(time_slot).split("-")
        start = datetime.strptime(start_text.strip(), "%H:%M")
        end = datetime.strptime(end_text.strip(), "%H:%M")
    except ValueError:
        return None
    return start.hour * 60 + start.minute, end.hour * 60 + end.minute


def slot_duration_hours(time_slot: str):
    """จำนวนชั่วโมงจริงของช่วงเวลา (รองรับเศษชั่วโมง เช่น 1.5) หรือ None"""
    minutes = parse_slot_minutes(time_slot)
    if minutes and minutes[1] > minutes[0]:
        return (minutes[1] - minutes[0]) / 60
    return None


def slots_overlap(slot_a: str, slot_b: str) -> bool:
    """ช่วงเวลา 2 ช่วงทับกันหรือไม่ (ถ้าอ่านรูปแบบไม่ได้ ให้เทียบข้อความตรง ๆ)"""
    a, b = parse_slot_minutes(slot_a), parse_slot_minutes(slot_b)
    if not a or not b:
        return slot_a == slot_b
    return a[0] < b[1] and b[0] < a[1]


def computer_state(record: dict, bookings: list[dict] | None = None, now: datetime | None = None):
    """สถานะจริงของเครื่อง (status, start, end) ใช้ร่วมกันทั้งตาราง 1 (Computer Report) และตาราง 2 (สถานะทุกเครื่อง 50+)
    - Maint            -> Maint (ไม่มีเวลา)
    - In Use           -> เริ่ม = เวลาเริ่มใช้งาน (Login) / สิ้นสุด = เริ่ม + เรตราคา
    - มีการจองที่ครอบคลุมเวลาปัจจุบัน -> In Use ตามเวลาเริ่ม/สิ้นสุดของการจอง
    - นอกนั้น            -> Available"""
    status = normalize_status(record["status"])
    if status == "Maint":
        return "Maint", None, None
    if status == "In Use":
        start, end = computer_use_times({**record, "status": "In Use"})
        return "In Use", start, end
    now = now or datetime.now()
    upcoming = []
    if bookings:
        for booking in bookings:
            if booking["is_active"] and booking["comp_id"] == record["comp_id"]:
                b_start, b_end = booking_times(booking)
                if b_start and b_end and b_start <= now < b_end:
                    return "In Use", b_start, b_end
                if b_start and b_end and now < b_start and b_start.date() == now.date():
                    upcoming.append((b_start, b_end))
    if upcoming:  # มีการจองที่ยังไม่ถึงเวลาในวันนี้ -> Booked (แสดงเวลาจองในตาราง)
        b_start, b_end = min(upcoming)
        return "Booked", b_start, b_end
    return "Available", None, None


def booking_times(booking: dict):
    """เวลาเริ่ม/สิ้นสุดของการจอง จากวันที่จอง + ช่วงเวลา (เช่น 13:00-16:00)
    ถ้าเป็นข้อมูลเก่าที่ไม่มีช่วงเวลา ใช้เวลาที่จอง + จำนวนชั่วโมง"""
    slot = booking.get("time_slot", "")
    if "-" in slot and booking.get("booking_date"):
        try:
            start_text, end_text = slot.split("-")
            start = datetime.strptime(f"{booking['booking_date']} {start_text}", "%Y-%m-%d %H:%M")
            end = datetime.strptime(f"{booking['booking_date']} {end_text}", "%Y-%m-%d %H:%M")
            return start, end
        except ValueError:
            pass
    start = parse_time(booking.get("booked_at", ""))
    return (start, start + timedelta(hours=booking["hours"])) if start else (None, None)


def computer_ready(record: dict) -> bool:
    """ความพร้อมของเครื่อง (คอลัมน์ Active): Available/In Use = พร้อม (Yes), Maint = ไม่พร้อม (No)
    เครื่องที่ถูกลบ (is_active = False) ถือว่าไม่พร้อมเช่นกัน"""
    return bool(record["is_active"]) and record["status"].strip().casefold() != "maint"


def _pc_number(name: str) -> int | None:
    """ดึงเลขท้ายชื่อเครื่อง เช่น PC-51 -> 51 (ไม่มีเลขท้าย -> None)"""
    match = re.search(r"(\d+)\s*$", name or "")
    return int(match.group(1)) if match else None


def compute_total_pcs(records: list[dict], bookings: list[dict] | None = None) -> int:
    """จำนวนเครื่องที่ใช้แสดงในตารางสถานะ = max(50, เลขเครื่องสูงสุดที่เคยเพิ่ม/จอง)
    เพิ่ม PC-51, PC-52, ... ได้เรื่อยๆ ตารางและ Summary จะขยายตามอัตโนมัติ"""
    if bookings is None:
        bookings = read_all_bookings()
    numbers = [_pc_number(r["pc_name"]) for r in records if r["is_active"]]
    numbers += [_pc_number(b.get("pc_name", "")) for b in bookings if b["is_active"]]
    return max([TOTAL_PCS] + [n for n in numbers if n])


def build_pc_status_map(records: list[dict], total_pcs: int | None = None) -> dict:
    """แมปเลขเครื่อง (PC-01..PC-N, N >= 50) -> (สถานะ, เริ่ม, สิ้นสุด) นับเฉพาะเครื่อง Active
    ใช้ร่วมกันทั้งตารางสถานะบน terminal และ Summary ใน summary_report.txt เพื่อให้ตัวเลขตรงกัน"""
    info_by_pc = {}
    bookings = read_all_bookings()
    if total_pcs is None:
        total_pcs = compute_total_pcs(records, bookings)
    now = datetime.now()
    for record in records:
        if not record["is_active"]:
            continue
        match = re.search(r"(\d+)\s*$", record["pc_name"])  # ดึงเลขท้ายชื่อ เช่น PC-11 -> 11
        if match:
            number = int(match.group(1))
            if 1 <= number <= total_pcs:
                status, use_start, use_end = computer_state(record, bookings, now)
                info_by_pc[number] = (status, show_time(use_start), show_time(use_end))
    registered_ids = {r["comp_id"] for r in records if r["is_active"]}
    for booking in bookings:
        if not booking["is_active"] or booking["comp_id"] in registered_ids:
            continue
        match = re.search(r"(\d+)\s*$", booking.get("pc_name", ""))
        if not match or not 1 <= int(match.group(1)) <= total_pcs or int(match.group(1)) in info_by_pc:
            continue
        b_start, b_end = booking_times(booking)
        if not b_start or not b_end:
            continue
        if b_start <= now < b_end:
            info_by_pc[int(match.group(1))] = ("In Use", show_time(b_start), show_time(b_end))
        elif now < b_start and b_start.date() == now.date():
            info_by_pc[int(match.group(1))] = ("Booked", show_time(b_start), show_time(b_end))
    return info_by_pc


def normalize_member_level(member_level: str) -> str:
    normalized_level = member_level.strip().casefold()
    level_aliases = {
        "vip": "VIP ระดับ 1",
        "vip1": "VIP ระดับ 1",
        "vip2": "VIP ระดับ 2",
        "vip3": "VIP ระดับ 3",
    }
    if normalized_level in level_aliases:
        return level_aliases[normalized_level]
    for level in VIP_DISCOUNTS:
        if normalized_level == level.casefold():
            return level
    if normalized_level == "ทั่วไป":
        return "ทั่วไป"
    return "ทั่วไป"


# ป้ายภาษาอังกฤษสำหรับแสดงในรายงาน (ค่าที่เก็บในไฟล์ยังเป็นเดิม)
MEMBER_LEVEL_EN = {
    "ทั่วไป": "General",
    "VIP ระดับ 1": "VIP Level 1",
    "VIP ระดับ 2": "VIP Level 2",
    "VIP ระดับ 3": "VIP Level 3",
}
ISSUE_TYPE_EN = {
    "RAM พัง": "RAM Failure",
    "จอเป็นเส้น": "Screen Lines",
    "จอพัง": "Screen Broken",
    "อื่นๆ": "Other",
}


def issue_text_en(text: str) -> str:
    """แปลประเภทปัญหาแบบ preset เป็นอังกฤษ (ข้อความที่ลูกค้าพิมพ์เองคงเดิม)"""
    return ISSUE_TYPE_EN.get(text, text)


def times_en(count: int) -> str:
    return f"{count} time" + ("" if count == 1 else "s")


def member_discount(member_level: str) -> float:
    return VIP_DISCOUNTS.get(normalize_member_level(member_level), 0.0)


def normalize_computer_zone(zone: str) -> str:
    zone_aliases = {
        "standard": "Standard",
        "vip": "VIP",
        "vip1": "VIP1",
        "vip2": "VIP2",
        "vip3": "VIP3",
    }
    return zone_aliases.get(zone.strip().casefold(), zone.strip())


# ==========================================
# BINARY I/O FUNCTIONS (COMPUTER RECORD)
# ==========================================
def pack_computer_record(comp_id: int, pc_name: str, zone: str, ram_gb: int, 
                         rate_per_hr: float, status: str, is_active: bool,
                         use_start: str = "", vip_user_id: int = 0) -> bytes:
    """แปลงข้อมูลคอมพิวเตอร์เป็น bytes ขนาด fixed-length ด้วย struct.pack"""
    return struct.pack(
        RECORD_FORMAT,
        comp_id,
        pc_name.encode('utf-8')[:16].ljust(16, b'\x00'),
        zone.encode('utf-8')[:16].ljust(16, b'\x00'),
        ram_gb,
        rate_per_hr,
        status.encode('utf-8')[:16].ljust(16, b'\x00'),
        is_active,
        use_start.encode('ascii')[:20].ljust(20, b'\x00'),
        vip_user_id,
    )


def unpack_computer_record(buffer: bytes) -> dict:
    """แปลง bytes กลับมาเป็น dictionary ข้อมูลคอมพิวเตอร์"""
    unpacked = struct.unpack(RECORD_FORMAT, buffer)
    return {
        "comp_id": unpacked[0],
        "pc_name": unpacked[1].decode('utf-8').rstrip('\x00'),
        "zone": normalize_computer_zone(unpacked[2].decode('utf-8').rstrip('\x00')),
        "ram_gb": unpacked[3],
        "rate_per_hr": unpacked[4],
        "raw_status": unpacked[5].decode('utf-8').rstrip('\x00'),
        "status": normalize_status(unpacked[5].decode('utf-8').rstrip('\x00')),
        "is_active": unpacked[6],
        "use_start": unpacked[7].decode('ascii').rstrip('\x00'),
        "vip_user_id": unpacked[8],
    }


def init_data_files():
    """สร้างไฟล์ข้อมูลเปล่าเมื่อยังไม่มีไฟล์ โดยไม่ใส่ข้อมูลตัวอย่าง"""
    for file_path in (FILE_COMPUTERS, FILE_CUSTOMERS, FILE_BOOKINGS):
        if not os.path.exists(file_path):
            with open(file_path, "wb") as file:
                file.flush()
                os.fsync(file.fileno())


def read_all_computers() -> list[dict]:
    """อ่านระเบียนคอมพิวเตอร์ทั้งหมดจากไฟล์ไบนารี"""
    records = []
    if not os.path.exists(FILE_COMPUTERS):
        return records

    file_size = os.path.getsize(FILE_COMPUTERS)
    old_size = None
    if file_size and file_size % RECORD_SIZE != 0:
        if file_size % PREVIOUS_RECORD_SIZE == 0:
            old_size = PREVIOUS_RECORD_SIZE   # ไม่มี VIP Member ID
        elif file_size % LEGACY_RECORD_SIZE == 0:
            old_size = LEGACY_RECORD_SIZE     # ไม่มีเวลาเริ่มใช้งาน + VIP Member ID
    if old_size:
        # ไฟล์รุ่นเก่า -> แปลงเป็นรูปแบบใหม่อัตโนมัติ (เติมช่องที่ขาดเป็นค่าว่าง/0)
        with open(FILE_COMPUTERS, "rb") as f:
            while chunk := f.read(old_size):
                if len(chunk) == old_size:
                    records.append(unpack_computer_record(chunk + b"\x00" * (RECORD_SIZE - old_size)))
        with open(FILE_COMPUTERS, "wb") as f:
            for r in records:
                f.write(pack_computer_record(
                    r["comp_id"], r["pc_name"], r["zone"], r["ram_gb"],
                    r["rate_per_hr"], r["status"], r["is_active"], r["use_start"], r["vip_user_id"]))
            f.flush()
            os.fsync(f.fileno())
        return records

    with open(FILE_COMPUTERS, "rb") as f:
        while chunk := f.read(RECORD_SIZE):
            if len(chunk) == RECORD_SIZE:
                records.append(unpack_computer_record(chunk))
    return records


def refresh_computers() -> None:
    """ทำให้ computers.bin ตรงกับกติกาก่อนสร้างรายงานทุกครั้ง
    1) สถานะพิมพ์เล็ก/ใหญ่ปนกัน -> แปลงเป็น Available / In Use / Maint
    2) มีรายงานปัญหา Open -> เครื่องต้องเป็น Maint (Active = No) และไม่มีเวลาใช้งาน
    3) สถานะ In Use แต่ยังไม่มีเวลาเริ่ม -> ประทับเวลา Login = ตอนนี้ (สิ้นสุด = เริ่ม + เรตราคา)"""
    records = read_all_computers()
    if not records:
        return
    open_comp_ids = {str(item["comp_id"]) for item in _read_problem_reports() if item["status"] == "Open"}
    now_text = datetime.now().strftime(TIME_FORMAT)
    changed = False
    for record in records:
        if not record["is_active"]:
            continue
        new_status, new_start = record["status"], record["use_start"]
        if str(record["comp_id"]) in open_comp_ids:
            new_status, new_start = "Maint", ""
        elif new_status == "In Use" and not parse_time(new_start):
            new_start = now_text
        elif new_status != "In Use":
            new_start = ""
        if (new_status != record["raw_status"] or new_start != record["use_start"]):
            record["status"], record["use_start"] = new_status, new_start
            record["dirty"] = True
            changed = True
    if not changed:
        return
    with open(FILE_COMPUTERS, "wb") as file:
        for record in records:
            file.write(pack_computer_record(
                record["comp_id"], record["pc_name"], record["zone"], record["ram_gb"],
                record["rate_per_hr"], record["status"], record["is_active"], record["use_start"],
                record["vip_user_id"]))
        file.flush()
        os.fsync(file.fileno())


def pack_booking(booking_id: int, comp_id: int, hours: int, booked_at: str,
                 is_active: bool = True, user_id: int = 0,
                 member_level: str = "ทั่วไป", discount_percent: float = 0.0,
                 booking_date: str = "", time_slot: str = "", pc_name: str = "") -> bytes:
    pc_name_bytes = pc_name.encode("utf-8")
    if len(pc_name_bytes) > 16:
        raise ValueError("PC Name exceeds its fixed binary length")
    level_bytes = normalize_member_level(member_level).encode("utf-8")
    date_bytes = (booking_date or booked_at[:10]).encode("ascii")
    time_slot_bytes = time_slot.encode("ascii")
    if len(level_bytes) > 24 or len(date_bytes) > 10 or len(time_slot_bytes) > 24:
        raise ValueError("Booking field exceeds its fixed binary length")
    return struct.pack(
        BOOKING_FORMAT,
        booking_id,
        comp_id,
        user_id,
        math.ceil(hours - 1e-9),
        RATE_PER_HOUR,
        discount_percent,
        level_bytes.ljust(24, b"\x00"),
        booked_at.encode("ascii").ljust(20, b"\x00"),
        date_bytes.ljust(10, b"\x00"),
        time_slot_bytes.ljust(24, b"\x00"),
        is_active,
        pc_name_bytes.ljust(16, b"\x00"),
    )


def unpack_booking(buffer: bytes, v2: bool = False) -> dict:
    if v2:   # ไฟล์รุ่นก่อน: ยังไม่มี PC Name
        (booking_id, comp_id, user_id, hours, rate_per_hour, discount_percent,
         member_level, booked_at, booking_date, time_slot, is_active) = struct.unpack(
            BOOKING_V2_FORMAT, buffer
        )
        pc_name = b""
    else:
        (booking_id, comp_id, user_id, hours, rate_per_hour, discount_percent,
         member_level, booked_at, booking_date, time_slot, is_active, pc_name) = struct.unpack(
            BOOKING_FORMAT, buffer
        )
    # ชั่วโมงจริงคำนวณจากช่วงเวลาที่จอง (เก็บใน .bin เป็นจำนวนเต็มปัดขึ้น)
    hours = slot_duration_hours(time_slot.decode("ascii").rstrip("\x00")) or hours
    subtotal = hours * rate_per_hour
    return {
        "booking_id": booking_id,
        "comp_id": comp_id,
        "user_id": user_id,
        "hours": hours,
        "rate_per_hour": rate_per_hour,
        "member_level": normalize_member_level(member_level.decode("utf-8").rstrip("\x00")),
        "discount_percent": discount_percent,
        "subtotal": subtotal,
        "total": subtotal * (1 - discount_percent / 100),
        "booked_at": booked_at.decode("ascii").rstrip("\x00"),
        "booking_date": booking_date.decode("ascii").rstrip("\x00"),
        "time_slot": time_slot.decode("ascii").rstrip("\x00"),
        "is_active": is_active,
        "pc_name": pc_name.decode("utf-8").rstrip("\x00"),
    }


def read_all_bookings() -> list[dict]:
    bookings = []
    if not os.path.exists(FILE_BOOKINGS):
        return bookings

    file_size = os.path.getsize(FILE_BOOKINGS)
    if file_size == 0:
        return bookings
    if file_size % BOOKING_SIZE == 0:
        record_format, record_size = "current", BOOKING_SIZE
    elif file_size % BOOKING_V2_SIZE == 0:
        record_format, record_size = "v2", BOOKING_V2_SIZE
    elif file_size % PREVIOUS_BOOKING_SIZE == 0:
        record_format, record_size = "previous", PREVIOUS_BOOKING_SIZE
    elif file_size % LEGACY_BOOKING_SIZE == 0:
        record_format, record_size = "legacy", LEGACY_BOOKING_SIZE
    else:
        raise ValueError("bookings.bin has an incomplete or unknown record format")

    with open(FILE_BOOKINGS, "rb") as file:
        while chunk := file.read(record_size):
            if len(chunk) != record_size:
                break
            if record_format == "current":
                bookings.append(unpack_booking(chunk))
                continue
            if record_format == "v2":
                bookings.append(unpack_booking(chunk, v2=True))
                continue

            if record_format == "previous":
                (booking_id, comp_id, user_id, hours, rate_per_hour,
                 discount_percent, member_level, booked_at, is_active) = struct.unpack(
                    PREVIOUS_BOOKING_FORMAT, chunk
                )
                member_level = normalize_member_level(
                    member_level.decode("utf-8").rstrip("\x00")
                )
                subtotal = hours * rate_per_hour
                bookings.append(
                    {
                        "booking_id": booking_id,
                        "comp_id": comp_id,
                        "user_id": user_id,
                        "hours": hours,
                        "rate_per_hour": rate_per_hour,
                        "member_level": member_level,
                        "discount_percent": discount_percent,
                        "subtotal": subtotal,
                        "total": subtotal * (1 - discount_percent / 100),
                        "booked_at": booked_at.decode("ascii").rstrip("\x00"),
                        "booking_date": booked_at.decode("ascii").rstrip("\x00")[:10],
                        "time_slot": "",
                        "is_active": is_active,
                    }
                )
                continue

            booking_id, comp_id, hours, rate_per_hour, booked_at, is_active = struct.unpack(
                LEGACY_BOOKING_FORMAT, chunk
            )
            subtotal = hours * rate_per_hour
            bookings.append(
                {
                    "booking_id": booking_id,
                    "comp_id": comp_id,
                    "user_id": 0,
                    "hours": hours,
                    "rate_per_hour": rate_per_hour,
                    "member_level": "ทั่วไป",
                    "discount_percent": 0.0,
                    "subtotal": subtotal,
                    "total": subtotal,
                    "booked_at": booked_at.decode("ascii").rstrip("\x00"),
                    "booking_date": booked_at.decode("ascii").rstrip("\x00")[:10],
                    "time_slot": "",
                    "is_active": is_active,
                }
            )
    return bookings


def write_all_bookings(bookings: list[dict]) -> None:
    with open(FILE_BOOKINGS, "wb") as file:
        for booking in bookings:
            file.write(
                pack_booking(
                    booking["booking_id"],
                    booking["comp_id"],
                    booking["hours"],
                    booking["booked_at"],
                    booking["is_active"],
                    booking.get("user_id", 0),
                    booking.get("member_level", "ทั่วไป"),
                    booking.get("discount_percent", 0.0),
                    booking.get("booking_date", ""),
                    booking.get("time_slot", ""),
                    booking.get("pc_name", ""),
                )
            )
        file.flush()
        os.fsync(file.fileno())


def pack_member_application(user_id: int, name: str, member_level: str,
                            applied_at: str, is_active: bool = True,
                            legacy_phone: str = "") -> bytes:
    name_bytes = name.encode("utf-8")
    phone_bytes = legacy_phone.encode("utf-8")
    level_bytes = member_level.encode("utf-8")
    if len(name_bytes) > 32 or len(phone_bytes) > 16 or len(level_bytes) > 24:
        raise ValueError("Member field exceeds its fixed binary length")
    return struct.pack(
        MEMBER_APPLICATION_FORMAT,
        user_id,
        name_bytes.ljust(32, b"\x00"),
        phone_bytes.ljust(16, b"\x00"),
        level_bytes.ljust(24, b"\x00"),
        applied_at.encode("ascii").ljust(20, b"\x00"),
        is_active,
    )


def unpack_member_application(buffer: bytes) -> dict:
    user_id, name, legacy_phone, member_level, applied_at, is_active = struct.unpack(
        MEMBER_APPLICATION_FORMAT, buffer
    )
    return {
        "user_id": user_id,
        "name": name.decode("utf-8").rstrip("\x00"),
        "legacy_phone": legacy_phone.decode("utf-8").rstrip("\x00"),
        "member_level": normalize_member_level(member_level.decode("utf-8").rstrip("\x00")),
        "applied_at": applied_at.decode("ascii").rstrip("\x00"),
        "is_active": is_active,
    }


def read_member_applications() -> list[dict]:
    applications = []
    if not os.path.exists(FILE_CUSTOMERS):
        return applications

    file_size = os.path.getsize(FILE_CUSTOMERS)
    is_new_format = file_size == 0 or (
        file_size % MEMBER_APPLICATION_SIZE == 0
        and file_size % LEGACY_MEMBER_APPLICATION_SIZE != 0
    )
    with open(FILE_CUSTOMERS, "rb") as file:
        record_size = MEMBER_APPLICATION_SIZE if is_new_format else LEGACY_MEMBER_APPLICATION_SIZE
        while chunk := file.read(record_size):
            if len(chunk) != record_size:
                break
            if is_new_format:
                applications.append(unpack_member_application(chunk))
                continue

            user_id, name, phone, applied_at, legacy_status = struct.unpack(
                LEGACY_MEMBER_APPLICATION_FORMAT, chunk
            )
            legacy_status = legacy_status.decode("utf-8").rstrip("\x00")
            member_level = normalize_member_level(legacy_status)
            applications.append(
                {
                    "user_id": user_id,
                    "name": name.decode("utf-8").rstrip("\x00"),
                    "legacy_phone": phone.decode("utf-8").rstrip("\x00"),
                    "member_level": member_level,
                    "applied_at": applied_at.decode("ascii").rstrip("\x00"),
                    "is_active": legacy_status != "Deleted",
                }
            )
    return applications


# ==========================================
# INPUT VALIDATION HELPERS
# ==========================================
def input_int(prompt: str, min_val: int = None, max_val: int = None) -> int:
    """ตรวจสอบอินพุตตัวเลขจำนวนเต็ม"""
    while True:
        val_str = input(prompt).strip()
        try:
            val = int(val_str)
            if min_val is not None and val < min_val:
                print(f"[!] ค่าต้องไม่น้อยกว่า {min_val}")
                continue
            if max_val is not None and val > max_val:
                print(f"[!] ค่าต้องไม่เกิน {max_val}")
                continue
            return val
        except ValueError:
            print("[!] อินพุตไม่ถูกต้อง: กรุณากรอกเป็นตัวเลขจำนวนเต็มเท่านั้น")


def input_float(prompt: str, min_val: float = None) -> float:
    """ตรวจสอบอินพุตตัวเลขทศนิยม"""
    while True:
        val_str = input(prompt).strip()
        try:
            val = float(val_str)
            if min_val is not None and val < min_val:
                print(f"[!] ค่าต้องไม่น้อยกว่า {min_val}")
                continue
            return val
        except ValueError:
            print("[!] อินพุตไม่ถูกต้อง: กรุณากรอกเป็นตัวเลขทศนิยมเท่านั้น")


def input_string(prompt: str, max_length: int = 15, choices: list = None) -> str:
    """ตรวจสอบอินพุตข้อความ พร้อมจำกัดความยาวและตัวเลือก"""
    while True:
        val = input(prompt).strip()
        if not val:
            print("[!] ข้อความต้องไม่เป็นค่าว่าง")
            continue
        if len(val.encode('utf-8')) > max_length:
            print(f"[!] ความยาวข้อความเกิน {max_length} ไบต์ (กรุณากรอกใหม่ให้สั้นลง)")
            continue
        if choices and val.lower() not in [c.lower() for c in choices]:
            print(f"[!] ค่าที่กรอกต้องเป็นหนึ่งในนี้เท่านั้น: {', '.join(choices)}")
            continue
        return val


# ==========================================
# REPORT GENERATION FUNCTION
# ==========================================
def generate_report(output_file: str | None = None) -> str:
    """สร้างรายงานข้อความ (.txt) พร้อมข้อมูลสรุป ช่องว่าง และประวัติการทำงาน"""
    if output_file is None:
        output_file = FILE_REPORT
    refresh_computers()
    records = read_all_computers()
    all_bookings = read_all_bookings()
    now = datetime.now()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S (+07:00)")

    lines = []
    lines.append("Cyber Cafe Booking System - Computer Report")
    lines.append(f"Generated At : {now_str}")
    lines.append("App Version  : 1.0")
    lines.append("Endianness   : Little-Endian")
    lines.append("Encoding     : UTF-8 (fixed-length)")
    lines.append("Data Sources : computers.bin, bookings.bin, customers.bin (+ problem_reports.csv)")
    lines.append("")

    total_records = len(records)
    active_recs = [r for r in records if r["is_active"]]
    deleted_count = total_records - len(active_recs)

    # สถานะรวมทั้ง TOTAL_PCS เครื่อง (เครื่องที่ยังไม่ลงทะเบียน = Available) ตรงกับตารางสถานะบน terminal
    total_pcs = compute_total_pcs(records)
    pc_info = build_pc_status_map(records, total_pcs)
    pc_status_counts = Counter(pc_info.get(n, ("Available",))[0] for n in range(1, total_pcs + 1))
    in_use_count = pc_status_counts["In Use"]
    available_count = pc_status_counts["Available"]
    maint_count = pc_status_counts["Maint"]
    booked_count = pc_status_counts["Booked"]

    rates = [r["rate_per_hr"] for r in active_recs]
    min_rate = min(rates) if rates else 0.0
    max_rate = max(rates) if rates else 0.0
    avg_rate = sum(rates) / len(rates) if rates else 0.0

    zone_counts = {}
    for r in active_recs:
        z = r["zone"]
        zone_counts[z] = zone_counts.get(z, 0) + 1

    # สร้างแถวข้อมูลตาราง
    table_rows = []
    members_by_id = {m["user_id"]: m for m in vip_members()}
    for r in records:
        duration_hours = r["rate_per_hr"] / RATE_PER_HOUR if RATE_PER_HOUR else 0
        shown_status, use_start, use_end = computer_state(r, all_bookings, now)
        if not r["is_active"]:
            shown_status, use_start, use_end = r["status"], None, None
        vip_label, vip_discount, vip_total = computer_vip_cost(r, members_by_id)
        table_rows.append([
            r["comp_id"], r["pc_name"], r["zone"], vip_label, r["ram_gb"],
            f"{r['rate_per_hr']:.2f} /{duration_hours:g} Hour",
            f"{vip_discount:g}%", f"{vip_total:.2f}",
            shown_status, show_time(use_start), show_time(use_end),
            "Yes" if (computer_ready(r) and shown_status != "Maint") else "No",
            sum(1 for b in all_bookings if b["is_active"] and b["comp_id"] == r["comp_id"]),
        ])
    lines.extend(format_table(
        ["CompID", "PC Name", "Member", "VIP Member", "RAM (GB)", "Rate (THB / Duration)",
         "Discount", "Total (THB)", "Status", "Start At", "End At", "Active", "Bookings"],
        table_rows, min_widths=[6, 10, 10, 18, 8, 21, 8, 11, 8, 16, 16, 6, 8], row_separators=True,
    ))
    bookings = [booking for booking in read_all_bookings() if booking["is_active"]]
    lines.append("")
    lines.extend(build_pc_status_lines(records))
    lines.append("")
    lines.append("รายชื่อสมาชิก VIP ทั้งหมด (VIP Members)")
    lines.extend(vip_members_table_lines(records))
    lines.append("")
    lines.append("ตารางการจอง (Booking Records - bookings.bin)")
    lines.extend(build_booking_table_lines(records, all_bookings))
    lines.append("")
    lines.append("Summary (นับเฉพาะเครื่องสถานะ Active)")
    summary_items = [
        ("Total Computers (records)", total_records),
        ("Active Computers", len(active_recs)),
        ("Deleted Computers", deleted_count),
        ("Free Slots (Unused/Soft-Deleted)", deleted_count),
        ("Total PCs (Status Table)", total_pcs),
        ("Currently In Use", in_use_count),
        ("Available Now", available_count),
        ("Booked Today (upcoming)", booked_count),
        ("Under Maintenance", maint_count),
        ("Open Problem Reports", sum(1 for item in _read_problem_reports() if item["status"] == "Open")),
        ("Active Bookings", len(bookings)),
        ("Total Booked Hours", f"{sum(booking['hours'] for booking in bookings):g}"),
        ("Booking Revenue (THB)", f"{sum(booking['total'] for booking in bookings):.2f}"),
        ("VIP Discounts (THB)", f"{sum(booking['subtotal'] - booking['total'] for booking in bookings):.2f}"),
        ("VIP Members (active)", len(members_by_id)),
        ("Computers Linked to VIP", sum(1 for r in active_recs if computer_vip_cost(r, members_by_id)[0] != "-")),
        ("Computer Package Total (THB)", f"{sum(computer_vip_cost(r, members_by_id)[2] for r in active_recs):.2f}"),
        ("Computer VIP Discounts (THB)", f"{sum(r['rate_per_hr'] - computer_vip_cost(r, members_by_id)[2] for r in active_recs):.2f}"),
    ]
    for label, value in summary_items:
        lines.append(f"- {pad(label, 32)} : {value}")
    lines.append("")
    lines.append("Package Price Statistics (THB, Active only)")
    lines.append(f"- Min : {min_rate:.2f}")
    lines.append(f"- Max : {max_rate:.2f}")
    lines.append(f"- Avg : {avg_rate:.2f}")
    lines.append("")
    lines.append("Computers by Member (Active only)")
    for z in sorted(zone_counts.keys()):
        lines.append(f"- {pad(z, 10)} : {zone_counts[z]}")

    # ประวัติการทำงานล่าสุด (Audit Logs)
    lines.append("")
    lines.append("Recent Activity Logs")
    if ACTION_LOGS:
        for log in ACTION_LOGS[-10:]:  # แสดงสูงสุด 10 รายการล่าสุด
            lines.append(f"- {log}")
    else:
        lines.append("- No activity recorded in current session.")

    report_content = "\n".join(lines)

    # เขียนลงไฟล์ .txt พร้อม flush & sync เพื่อความปลอดภัย
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report_content)
        f.flush()
        os.fsync(f.fileno())

    return report_content


def _read_problem_reports() -> list[dict[str, str]]:
    if not os.path.exists(FILE_ISSUES):
        return []
    with open(FILE_ISSUES, "r", newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _write_problem_reports(reports: list[dict[str, str]]) -> None:
    with open(FILE_ISSUES, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ISSUE_FIELDS)
        writer.writeheader()
        writer.writerows(reports)
        file.flush()
        os.fsync(file.fileno())


def sync_computer_with_issues(comp_id: int, reports: list[dict[str, str]] | None = None) -> None:
    """เชื่อม problem_reports -> สถานะเครื่องใน computers.bin และ summary_report.txt
    - มีรายงานปัญหา Open อย่างน้อย 1 รายการ -> เครื่องเป็น Maint (หยุดใช้งาน/หยุดจอง)
    - ไม่เหลือรายงาน Open และเครื่องยัง Maint อยู่ -> กลับเป็น Available
    แล้วสร้าง summary_report.txt ใหม่ทันที"""
    if reports is None:
        reports = _read_problem_reports()
    has_open = any(str(item["comp_id"]) == str(comp_id) and item["status"] == "Open" for item in reports)

    records = read_all_computers()
    idx = next((i for i, r in enumerate(records) if r["comp_id"] == comp_id and r["is_active"]), None)
    if idx is None:
        generate_report()
        return
    record = records[idx]
    is_maint = record["status"].strip().casefold() == "maint"

    if has_open and not is_maint:
        new_status = "Maint"
    elif not has_open and is_maint:
        new_status = "Available"
    else:
        generate_report()  # สถานะไม่เปลี่ยน แต่อัปเดตตัวเลข Open Problem Reports ใน Summary
        if has_open and is_maint:
            open_ids = ", ".join(
                f"#{item['report_id']}" for item in reports
                if str(item["comp_id"]) == str(comp_id) and item["status"] == "Open")
            print(f"[i] เครื่อง {record['pc_name']} (CompID {comp_id}) ยังเป็น Maint "
                  f"เพราะยังมีรายงานปัญหา Open อีก: {open_ids} (ปิดให้ครบจึงจะกลับเป็น Available / Active = Yes)")
        return

    with open(FILE_COMPUTERS, "rb+") as file:
        file.seek(idx * RECORD_SIZE)
        file.write(pack_computer_record(
            record["comp_id"], record["pc_name"], record["zone"], record["ram_gb"],
            record["rate_per_hr"], new_status, True, "", record["vip_user_id"]))  # Maint/Available ไม่มีเวลาใช้งาน
        file.flush()
        os.fsync(file.fileno())
    generate_report()
    log_action(f"Computer CompID {comp_id} status {record['status']} -> {new_status} (from problem reports)")
    print(f"[i] เครื่อง {record['pc_name']} (CompID {comp_id}) เปลี่ยนสถานะ {record['status']} -> {new_status} "
          f"และอัปเดต {FILE_REPORT} แล้ว")


def _issue_date(report: dict) -> str:
    """วันที่เกิดปัญหา (ข้อมูลเก่าที่ไม่มี issue_date ให้ใช้วันที่แจ้งแทน)"""
    return report.get("issue_date") or report.get("reported_at", "")[:10]


def _issue_datetime(report: dict) -> str:
    """วันเวลาที่แจ้งปัญหา (YYYY-MM-DD HH:MM) ข้อมูลเก่าที่ไม่มีเวลาใช้วันที่เดิมแทน"""
    reported_at = report.get("reported_at", "")
    return reported_at[:16] if len(reported_at) >= 16 else _issue_date(report)


def _issue_type(report: dict) -> str:
    return report.get("issue_type") or CUSTOM_ISSUE


def generate_issue_report(reports: list[dict[str, str]] | None = None) -> str:
    if reports is None:
        reports = _read_problem_reports()

    lines = [
        "Cyber Cafe Booking System - Problem Report",
        f"Generated At : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Encoding     : UTF-8",
        "Data Sources : problem_reports.csv, computers.bin, bookings.bin",
        "",
    ]
    lines.extend(format_table(
        ["ReportID", "CompID", "PC Name", "Date", "Problem", "Status"],
        [
            [r["report_id"], r["comp_id"], clip(r["pc_name"], 10), _issue_datetime(r),
             clip(issue_text_en(r["description"]), 24), r["status"]]
            for r in reports
        ],
        min_widths=[8, 6, 10, 16, 24, 8], bottom_border=True,
    ))

    # ---- Cross-reference: computers.bin (สถานะเครื่อง) + bookings.bin (การจองของเครื่องนั้น) ----
    comp_records = {r["comp_id"]: r for r in read_all_computers() if r["is_active"]}
    all_bookings = read_all_bookings()
    now = datetime.now()
    xref_rows = []
    linked = 0
    open_with_booking = 0
    for r in reports:
        if r["status"] == "Deleted":
            continue
        try:
            cid = int(r["comp_id"])
        except (TypeError, ValueError):
            cid = None
        rec = comp_records.get(cid)
        active_bk = [b for b in all_bookings if b["is_active"] and b["comp_id"] == cid]
        if rec:
            linked += 1
            state = computer_state(rec, all_bookings, now)[0]
            bin_name = rec["pc_name"]
        else:
            state, bin_name = "Not in computers.bin", "-"
        if r["status"] == "Open" and active_bk:
            open_with_booking += 1
        xref_rows.append([
            r["report_id"], r["comp_id"], clip(bin_name, 10), state,
            len(active_bk), f"{sum(b['hours'] for b in active_bk):g}",
        ])
    lines.extend(["", "Cross-Reference (computers.bin / bookings.bin)"])
    lines.extend(format_table(
        ["ReportID", "CompID", "PC (computers.bin)", "Status Now", "Active Bookings", "Booked Hrs"],
        xref_rows,
        min_widths=[8, 6, 18, 20, 15, 10], bottom_border=True,
    ))
    lines.append(f"- Reports matched in computers.bin     : {linked} / {len(xref_rows)}")
    lines.append(f"- Open reports on PCs with bookings    : {open_with_booking}  (bookings.bin)")

    # ---- Monthly summary: failures per PC / most common cause ----
    valid = [item for item in reports if item["status"] != "Deleted"]
    by_month = defaultdict(lambda: defaultdict(list))
    for item in valid:
        month = _issue_date(item)[:7]
        by_month[month][(item["comp_id"], item["pc_name"])].append(item)

    lines.extend(["", "Monthly Breakdown"])
    if not by_month:
        lines.append("- No problem reports yet")
    for month in sorted(by_month):
        pcs = by_month[month]
        month_total = sum(len(items) for items in pcs.values())
        month_types = Counter(_issue_type(i) for items in pcs.values() for i in items)
        top_type, top_count = month_types.most_common(1)[0]
        lines.extend(["", f"[{month}] Total failures: {month_total} | Most common issue: {issue_text_en(top_type)} ({times_en(top_count)})"])
        month_rows = []
        for (comp_id, pc_name), items in sorted(pcs.items(), key=lambda kv: -len(kv[1])):
            types = Counter(_issue_type(i) for i in items)
            top_type, top_count = types.most_common(1)[0]
            detail = ", ".join(f"{issue_text_en(name)} {count}" for name, count in types.most_common())
            month_rows.append([
                comp_id, pc_name, len(items),
                f"{issue_text_en(top_type)} ({times_en(top_count)})", detail,
            ])
        lines.extend(format_table(
            ["CompID", "PC Name", "Failures", "Most Frequent Issue", "Breakdown by Issue"],
            month_rows, min_widths=[6, 10, 8, 24, 20], bottom_border=True,
        ))

    overall = Counter(_issue_type(i) for i in valid)
    lines.extend(
        [
            "",
            "Summary",
            f"- Total Problem Reports : {len(valid)}",
            f"- Open                 : {sum(item['status'] == 'Open' for item in valid)}",
            f"- Closed               : {sum(item['status'] == 'Closed' for item in valid)}",
            "- Top Issue (all time)  : "
            + (f"{issue_text_en(overall.most_common(1)[0][0])} ({times_en(overall.most_common(1)[0][1])})" if overall else "-"),
        ]
    )

    content = "\n".join(lines)
    with open(FILE_ISSUE_REPORT, "w", encoding="utf-8") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    return content


def _display_problem_reports(reports: list[dict[str, str]]) -> None:
    print(generate_issue_report(reports))


def _display_computer_status_table(computers: list[dict]) -> None:
    print("\nสถานะการใช้งานเครื่อง:")
    print("\n".join(format_table(
        ["CompID", "PC Name", "Zone", "สถานะการใช้งาน"],
        [
            [c["comp_id"], c["pc_name"], c["zone"], c["status"]]
            for c in sorted(computers, key=lambda item: item["comp_id"])
        ],
        min_widths=[8, 16, 12, 16],
    )))


def _ask_issue_date() -> str | None:
    text = input("วันที่พบปัญหา (YYYY-MM-DD, Enter = วันนี้): ").strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print("[!] รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ YYYY-MM-DD")
        return None


def _ask_issue_type() -> tuple[str, str]:
    """คืนค่า (issue_type, description) จาก preset หรือให้ลูกค้ากรอกเอง"""
    print("ประเภทปัญหา:")
    for key, name in ISSUE_TYPES.items():
        label = f"{name} (กรอกเอง)" if name == CUSTOM_ISSUE else name
        print(f"{key}) {label}")
    choice = input_string(
        f"เลือกประเภทปัญหา (1-{len(ISSUE_TYPES)}): ", max_length=1, choices=list(ISSUE_TYPES)
    )
    issue_type = ISSUE_TYPES[choice]
    if issue_type == CUSTOM_ISSUE:
        description = input_string("กรอกปัญหาที่พบ: ", max_length=200)
    else:
        description = issue_type
    return issue_type, description


def _add_problem_report(computers: list[dict]) -> None:
    """จำลองลูกค้ารายงานปัญหา: วันที่ -> เครื่อง -> ประเภทปัญหา"""
    active_computers = [computer for computer in computers if computer["is_active"]]
    if not active_computers:
        print("[!] ยังไม่มีเครื่องในระบบ จึงรายงานปัญหาไม่ได้")
        return

    issue_date = datetime.now().strftime("%Y-%m-%d")  # วัน-เวลาที่แจ้งใช้เวลาปัจจุบันอัตโนมัติ

    _display_computer_status_table(active_computers)
    comp_id = input_int("CompID ที่พบปัญหา: ", min_val=1)
    computer = next((item for item in active_computers if item["comp_id"] == comp_id), None)
    if computer is None:
        print("[!] ไม่พบ CompID ที่ใช้งานอยู่")
        return

    issue_type, description = _ask_issue_type()

    reports = _read_problem_reports()
    report_id = max((int(item["report_id"]) for item in reports), default=0) + 1
    reports.append(
        {
            "report_id": str(report_id),
            "comp_id": str(comp_id),
            "pc_name": computer["pc_name"],
            "issue_date": issue_date,
            "issue_type": issue_type,
            "description": description,
            "reported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Open",
        }
    )
    _write_problem_reports(reports)
    generate_issue_report(reports)
    log_action(f"Problem report #{report_id}: CompID {comp_id} {issue_type} on {issue_date}")
    print(f"[✓] บันทึกรายงานปัญหา #{report_id} แล้ว ({issue_date} | {computer['pc_name']} | {description})")
    sync_computer_with_issues(comp_id, reports)  # เครื่องที่แจ้งปัญหา -> Maint


def _update_problem_report() -> None:
    reports = _read_problem_reports()
    report_id = input_int("หมายเลขรายงานที่ต้องการแก้ไข: ", min_val=1)
    report = next(
        (item for item in reports if int(item["report_id"]) == report_id and item["status"] != "Deleted"),
        None,
    )
    if report is None:
        print("[!] ไม่พบรายงาน หรือรายงานถูกลบแล้ว")
        return

    print(
        f"ปัจจุบัน: {_issue_date(report)} | {report['pc_name']} | "
        f"{_issue_type(report)} | {report['description']} | สถานะ: {report['status']}"
    )
    if input("แก้ไขประเภทปัญหา? (y = แก้, Enter = คงเดิม): ").strip().lower() == "y":
        report["issue_type"], report["description"] = _ask_issue_type()

    status = input("สถานะใหม่ (Open/Closed, Enter เพื่อคงเดิม): ").strip().title()
    if status and status not in ("Open", "Closed"):
        print("[!] สถานะไม่ถูกต้อง จึงคงสถานะเดิมไว้")
        status = ""
    if status:
        report["status"] = status

    _write_problem_reports(reports)
    generate_issue_report(reports)
    log_action(f"Updated problem report ID: {report_id}")
    print("[✓] แก้ไขรายงานปัญหาแล้ว")
    sync_computer_with_issues(int(report["comp_id"]), reports)  # Closed -> เครื่องกลับ Available / Open -> Maint


def _delete_problem_report() -> None:
    reports = _read_problem_reports()
    report_id = input_int("หมายเลขรายงานที่ต้องการลบ: ", min_val=1)
    remaining_reports = [item for item in reports if int(item["report_id"]) != report_id]
    if len(remaining_reports) == len(reports):
        print("[!] ไม่พบหมายเลขรายงานนี้")
        return
    deleted_comp_id = next(int(item["comp_id"]) for item in reports if int(item["report_id"]) == report_id)

    _write_problem_reports(remaining_reports)
    generate_issue_report(remaining_reports)
    log_action(f"Deleted problem report ID: {report_id}")
    print("[✓] ลบรายงานปัญหาและข้อมูลที่เกี่ยวข้องแล้ว")
    sync_computer_with_issues(deleted_comp_id, remaining_reports)  # ไม่เหลือปัญหา Open -> เครื่องกลับ Available


def issue_management_menu(read_computers) -> None:
    while True:
        print("\n--- จำลองลูกค้ารายงานปัญหา ---")
        print("1) Add (ลูกค้าแจ้งปัญหา)")
        print("2) Update (แก้ไข)")
        print("3) Delete (ลบ)")
        print("4) View (ดู)")
        print("5) Generate Report (.txt)")
        print("0) ย้อนกลับหน้าหลัก")
        choice = input("เลือกเมนู (0-5): ").strip()

        if choice == "1":
            _add_problem_report(read_computers())
        elif choice == "2":
            _update_problem_report()
        elif choice == "3":
            _delete_problem_report()
        elif choice == "4":
            _display_problem_reports(_read_problem_reports())
        elif choice == "5":
            print(generate_issue_report())
        elif choice == "0":
            break
        else:
            print("[!] ตัวเลือกไม่ถูกต้อง กรุณาเลือก 0 ถึง 5")


def generate_member_report(applications: list[dict] | None = None) -> str:
    if applications is None:
        applications = read_member_applications()

    active_applications = [item for item in applications if item["is_active"]]
    deleted_count = len(applications) - len(active_applications)
    level_order = {
        "ทั่วไป": 0,
        "VIP ระดับ 1": 1,
        "VIP ระดับ 2": 2,
        "VIP ระดับ 3": 3,
    }
    active_applications.sort(
        key=lambda item: (
            level_order.get(normalize_member_level(item["member_level"]), 4),
            item["user_id"],
        )
    )
    category_labels = {
        "ทั่วไป": f"{MEMBER_LEVEL_EN['ทั่วไป']} (0%)",
        **{
            level: f"{MEMBER_LEVEL_EN[level]} ({discount:g}%)"
            for level, discount in VIP_DISCOUNTS.items()
        },
    }
    lines = [
        "Cyber Cafe Booking System - Member Report",
        f"Generated At : {datetime.now().strftime('%Y-%m-%d %H:%M:%S (+07:00)')}",
        "Endianness   : Little-Endian",
        "Encoding     : UTF-8 (fixed-length)",
        "Data Sources : customers.bin, computers.bin, bookings.bin",
        "",
    ]
    # computers.bin: เครื่องที่ผูกกับสมาชิก / bookings.bin: การจองของสมาชิก
    comp_records = [r for r in read_all_computers() if r["is_active"]]
    member_bookings = [b for b in read_all_bookings() if b["is_active"]]

    def linked_pcs(user_id: int) -> str:
        pcs = ", ".join(r["pc_name"] for r in comp_records
                        if r["vip_user_id"] == user_id and is_vip_zone(r["zone"]))
        return pcs or "-"

    def member_booking_stats(user_id: int):
        mine = [b for b in member_bookings if b["user_id"] == user_id]
        return len(mine), sum(b["total"] for b in mine)

    lines.extend(format_table(
        ["User ID", "Full Name", "Member Level", "Applied At",
         "Linked PC (computers.bin)", "Bookings (bookings.bin)", "Booking Total (THB)"],
        [
            [a["user_id"], a["name"],
             category_labels[normalize_member_level(a["member_level"])], a["applied_at"],
             linked_pcs(a["user_id"]),
             member_booking_stats(a["user_id"])[0],
             f"{member_booking_stats(a['user_id'])[1]:.2f}"]
            for a in active_applications
        ],
        min_widths=[12, 32, 22, 19, 25, 23, 19], bottom_border=True,
    ))

    lines.extend(
        [
            "",
            "Summary",
            f"- Total Applications : {len(applications)}",
            f"- Active Members     : {len(active_applications)}",
            f"- Deleted Members    : {deleted_count}",
            f"- General (0%)       : {sum(normalize_member_level(item['member_level']) == 'ทั่วไป' for item in active_applications)}",
            f"- VIP Level 1 (10%)  : {sum(normalize_member_level(item['member_level']) == 'VIP ระดับ 1' for item in active_applications)}",
            f"- VIP Level 2 (13%)  : {sum(normalize_member_level(item['member_level']) == 'VIP ระดับ 2' for item in active_applications)}",
            f"- VIP Level 3 (20%)  : {sum(normalize_member_level(item['member_level']) == 'VIP ระดับ 3' for item in active_applications)}",
            f"- Members linked to a PC (computers.bin) : {sum(linked_pcs(a['user_id']) != '-' for a in active_applications)}",
            f"- Active bookings by members (bookings.bin) : {sum(member_booking_stats(a['user_id'])[0] for a in active_applications)}",
        ]
    )

    report_content = "\n".join(lines)
    with open(FILE_MEMBER_REPORT, "w", encoding="utf-8") as file:
        file.write(report_content)
        file.flush()
        os.fsync(file.fileno())
    return report_content


def add_member_application() -> None:
    print("\n--- สมัครสมาชิก ---")
    name = input_string("ชื่อ-นามสกุล (ไม่เกิน 32 ไบต์): ", max_length=32)
    member_level = input_string(
        "ระดับสมาชิก (ทั่วไป / vip1: ลด 10% / vip2: ลด 13% / vip3: ลด 20%): ",
        max_length=24,
        choices=[
            "ทั่วไป", "vip1", "vip2", "vip3",
            "VIP ระดับ 1", "VIP ระดับ 2", "VIP ระดับ 3",
        ],
    )
    member_level = normalize_member_level(member_level)

    applications = read_member_applications()
    user_id = max((item["user_id"] for item in applications), default=0) + 1
    applied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    applications.append(
        {
            "user_id": user_id,
            "name": name,
            "legacy_phone": "",
            "member_level": member_level,
            "applied_at": applied_at,
            "is_active": True,
        }
    )
    write_member_applications(applications)
    generate_member_report(applications)
    log_action(f"Added member user ID: {user_id} ({name})")
    print(f"[✓] บันทึกสมาชิก ID USER {user_id} แล้ว")


def write_member_applications(applications: list[dict]) -> None:
    with open(FILE_CUSTOMERS, "wb") as file:
        for application in applications:
            file.write(
                pack_member_application(
                    application["user_id"],
                    application["name"],
                    application["member_level"],
                    application["applied_at"],
                    application["is_active"],
                    application.get("legacy_phone", ""),
                )
            )
        file.flush()
        os.fsync(file.fileno())


def update_member_application() -> None:
    applications = read_member_applications()
    user_id = input_int("ID USER ที่ต้องการแก้ไข: ", min_val=1)
    application = next(
        (item for item in applications if item["user_id"] == user_id and item["is_active"]),
        None,
    )
    if application is None:
        print("[!] ไม่พบสมาชิก หรือสมาชิกถูกลบแล้ว")
        return

    name = input("ชื่อ-สกุลใหม่ (Enter เพื่อคงเดิม): ").strip()
    if name:
        if len(name.encode("utf-8")) > 32:
            print("[!] ชื่อยาวเกิน 32 ไบต์ จึงคงชื่อเดิมไว้")
        else:
            application["name"] = name

    member_level = input(
        "ระดับสมาชิกใหม่ (ทั่วไป/vip1/vip2/vip3, Enter เพื่อคงเดิม): "
    ).strip()
    if member_level:
        normalized_level = normalize_member_level(member_level)
        if member_level == "ทั่วไป" or normalized_level in VIP_DISCOUNTS:
            application["member_level"] = normalized_level
        else:
            print("[!] ระดับสมาชิกไม่ถูกต้อง จึงคงระดับเดิมไว้")

    write_member_applications(applications)
    generate_member_report(applications)
    log_action(f"Updated member user ID: {user_id}")
    print("[✓] แก้ไขข้อมูลสมาชิกแล้ว")


def delete_member_application() -> None:
    applications = read_member_applications()
    user_id = input_int("ID USER ที่ต้องการลบ: ", min_val=1)
    remaining_applications = [
        item for item in applications if item["user_id"] != user_id
    ]
    if len(remaining_applications) == len(applications):
        print("[!] ไม่พบ ID USER นี้")
        return

    write_member_applications(remaining_applications)
    generate_member_report(remaining_applications)
    log_action(f"Deleted member user ID: {user_id}")
    print("[✓] ลบสมาชิกและข้อมูลที่เกี่ยวข้องแล้ว")


def member_application_management_menu() -> None:
    while True:
        print("\n--- การสมัครสมาชิก ---")
        print("1) Add (เพิ่ม)")
        print("2) Update (แก้ไข)")
        print("3) Delete (ลบ)")
        print("4) View (ดู)")
        print("5) Generate Report (.txt)")
        print("0) ย้อนกลับหน้าหลัก")
        choice = input("เลือกเมนู (0-5): ").strip()

        if choice == "1":
            add_member_application()
        elif choice == "2":
            update_member_application()
        elif choice == "3":
            delete_member_application()
        elif choice == "4":
            report = generate_member_report()
            print("\n" + report)
        elif choice == "5":
            report = generate_member_report()
            print(f"[✓] สร้างรายงาน '{FILE_MEMBER_REPORT}' สำเร็จแล้ว")
            print("\n" + report)
        elif choice == "0":
            break
        else:
            print("[!] ตัวเลือกไม่ถูกต้อง กรุณาเลือก 0 ถึง 5")


# ==========================================
# CRUD OPERATIONS
# ==========================================
def ask_custom_time_slot():
    """ถามเวลาเริ่ม-สิ้นสุดเอง (HH:MM) คืน ('HH:MM-HH:MM', ชั่วโมง) หรือ None ถ้ากด Enter เพื่อยกเลิก"""
    while True:
        start_text = input("เวลาเริ่ม (HH:MM เช่น 09:30, Enter = ยกเลิก): ").strip()
        if not start_text:
            return None
        end_text = input("เวลาสิ้นสุด (HH:MM เช่น 12:00): ").strip()
        try:
            start = datetime.strptime(start_text, "%H:%M")
            end = datetime.strptime(end_text, "%H:%M")
        except ValueError:
            print("[!] รูปแบบเวลาไม่ถูกต้อง กรุณาใช้ HH:MM (00:00 - 23:59)")
            continue
        if end <= start:
            print("[!] เวลาสิ้นสุดต้องมากกว่าเวลาเริ่ม (จองได้ภายในวันเดียวกัน)")
            continue
        time_slot = f"{start:%H:%M}-{end:%H:%M}"
        return time_slot, slot_duration_hours(time_slot)


def book_computer():
    print("\nรายชื่อสมาชิก VIP ทั้งหมด (VIP Members):")
    print("\n".join(vip_members_table_lines()))
    booking_date_input = input("วันที่จอง (YYYY-MM-DD, Enter = วันนี้): ").strip()
    if booking_date_input:
        try:
            booking_date = datetime.strptime(booking_date_input, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            print("[!] รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ YYYY-MM-DD")
            return
    else:
        booking_date = datetime.now().strftime("%Y-%m-%d")

    print("ช่วงเวลาจอง:")
    for slot_id, (slot, hours) in BOOKING_SLOTS.items():
        print(f"{slot_id}) {slot} ({hours} ชั่วโมง)")
    custom_id = str(len(BOOKING_SLOTS) + 1)
    print(f"{custom_id}) กำหนดเวลาเอง (Custom)")
    slot_id = input_string(f"เลือกช่วงเวลา (1-{custom_id}): ", max_length=1,
                           choices=[*BOOKING_SLOTS, custom_id])
    if slot_id == custom_id:
        custom = ask_custom_time_slot()
        if custom is None:
            print("[!] ยกเลิกการจอง")
            return
        time_slot, hours = custom
    else:
        time_slot, hours = BOOKING_SLOTS[slot_id]

    zone_input = input_string(
        "Enter Member (Standard/VIP; VIP, vip1, vip2, vip3): ",
        max_length=15,
        choices=["Standard", "VIP", "vip1", "vip2", "vip3"],
    ).casefold()
    selected_zone = normalize_computer_zone(zone_input)

    def has_conflict(target_comp_id: int) -> bool:
        return any(
            booking["is_active"]
            and booking["comp_id"] == target_comp_id
            and booking["booking_date"] == booking_date
            and slots_overlap(booking["time_slot"], time_slot)
            for booking in bookings
        )

    bookings = read_all_bookings()
    records = read_all_computers()
    available = [
        computer for computer in records
        if computer["is_active"]
        and computer["status"].lower() != "maint"
        and normalize_computer_zone(computer["zone"]) == selected_zone
        and not has_conflict(computer["comp_id"])
    ]

    print(f"\nเครื่องว่าง Member {selected_zone} วันที่ {booking_date} ช่วง {time_slot}:")
    if available:
        for computer in available:
            print(f"{computer['comp_id']} - {computer['pc_name']} ({computer['zone']})")
    else:
        print("- ไม่มีเครื่องที่ลงทะเบียนว่างในช่วงนี้")
    print("(พิมพ์ CompID / PC Name เองได้ แม้ไม่อยู่ในรายการด้านบน)")

    comp_id = input_int("CompID ที่ต้องการจอง (พิมพ์เอง): ", min_val=1)
    registered = next((c for c in records if c["is_active"] and c["comp_id"] == comp_id), None)
    if registered and registered["status"].lower() == "maint":
        print(f"[!] CompID {comp_id} ({registered['pc_name']}) อยู่ในสถานะ Maint จองไม่ได้")
        return
    if has_conflict(comp_id):
        print(f"[!] CompID {comp_id} มีการจองทับช่วง {time_slot} วันที่ {booking_date} แล้ว")
        return

    default_pc = registered["pc_name"] if registered else ""
    pc_prompt = (f"PC Name (Enter = {default_pc}): " if default_pc
                 else "PC Name (พิมพ์เอง เช่น PC-30): ")
    while True:
        pc_name = input(pc_prompt).strip() or default_pc
        if not pc_name:
            print("[!] PC Name ต้องไม่เป็นค่าว่าง")
            continue
        if len(pc_name.encode("utf-8")) > 15:
            print("[!] PC Name ยาวเกิน 15 ไบต์ กรุณากรอกใหม่ให้สั้นลง")
            continue
        break
    if registered and pc_name != registered["pc_name"]:
        print(f"[i] ชื่อที่กรอกต่างจาก computers.bin ({registered['pc_name']}) "
              f"จะบันทึกชื่อ '{pc_name}' ในรายการจอง")
    elif not registered:
        print(f"[i] CompID {comp_id} ยังไม่อยู่ใน computers.bin จะบันทึกเฉพาะในรายการจอง")

    active_vip_members = [
        member
        for member in read_member_applications()
        if member["is_active"] and member_discount(member["member_level"]) > 0
    ]
    user_id = 0
    member_level = "ทั่วไป"
    if not active_vip_members:
        print("[!] ไม่มีสมาชิก VIP ที่ใช้งานอยู่")

    # เครื่องที่ผูกกับสมาชิก VIP ไว้แล้ว: กด Enter = ใช้สมาชิกคนนั้นคิดส่วนลดให้อัตโนมัติ
    default_member = next(
        (m for m in active_vip_members
         if registered and m["user_id"] == registered["vip_user_id"]), None)
    id_prompt = (f"ID USER VIP (Enter = {default_member['user_id']} {default_member['name']}, 0 = ข้าม): "
                 if default_member else "ID USER VIP (กด Enter เพื่อข้าม): ")
    while True:
        user_id_text = input(id_prompt).strip()
        if not user_id_text and default_member:
            user_id = default_member["user_id"]
            member_level = normalize_member_level(default_member["member_level"])
            break
        if not user_id_text or user_id_text == "0":
            break
        if user_id_text.isdigit():
            user_id = int(user_id_text)
            member = next(
                (
                    item
                    for item in active_vip_members
                    if item["user_id"] == user_id
                ),
                None,
            )
            if member is not None:
                member_level = normalize_member_level(member["member_level"])
                break
            print("[!] ไม่พบ ID USER VIP ที่ใช้งานอยู่ กรุณากรอกใหม่ หรือกด Enter เพื่อข้าม")
        else:
            print("[!] กรุณากรอก ID USER เป็นตัวเลข หรือกด Enter เพื่อข้าม")

    # ระดับ member: Enter = ตามสมาชิกที่เลือก (หรือทั่วไป) / พิมพ์เองเพื่อกำหนดระดับได้
    level_aliases = {
        "ทั่วไป": "ทั่วไป", "general": "ทั่วไป", "standard": "ทั่วไป", "0": "ทั่วไป",
        "vip": "VIP ระดับ 1", "vip1": "VIP ระดับ 1", "vip2": "VIP ระดับ 2", "vip3": "VIP ระดับ 3",
        **{level.casefold(): level for level in VIP_DISCOUNTS},
    }
    level_prompt = (f"ระดับ member (ทั่วไป / vip1 / vip2 / vip3, "
                    f"Enter = {MEMBER_LEVEL_EN[member_level]}): ")
    while True:
        level_text = input(level_prompt).strip().casefold()
        if not level_text:
            break
        if level_text in level_aliases:
            typed_level = level_aliases[level_text]
            if user_id and typed_level != member_level:
                print(f"[i] ใช้ระดับที่กรอก ({MEMBER_LEVEL_EN[typed_level]}) "
                      f"แทนระดับของสมาชิก ID {user_id} ({MEMBER_LEVEL_EN[member_level]})")
            member_level = typed_level
            break
        print("[!] ระดับ member ต้องเป็น ทั่วไป / vip1 / vip2 / vip3")

    discount_percent = member_discount(member_level)
    subtotal = hours * RATE_PER_HOUR
    total = subtotal * (1 - discount_percent / 100)
    booking_id = max((booking["booking_id"] for booking in bookings), default=0) + 1
    booked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    booking = {
        "booking_id": booking_id,
        "comp_id": comp_id,
        "pc_name": pc_name,
        "user_id": user_id,
        "member_level": member_level,
        "hours": hours,
        "rate_per_hour": RATE_PER_HOUR,
        "discount_percent": discount_percent,
        "subtotal": subtotal,
        "total": total,
        "booked_at": booked_at,
        "booking_date": booking_date,
        "time_slot": time_slot,
        "is_active": True,
    }
    bookings.append(booking)
    write_all_bookings(bookings)

    log_action(f"Booked CompID: {comp_id} ({pc_name}) on {booking_date} {time_slot}")
    generate_report()
    start_dt, end_dt = booking_times(booking)
    print(
        f"[✓] จอง {pc_name} (CompID {comp_id}) สำเร็จวันที่ {booking_date} เวลา {time_slot} "
        f"(เริ่ม {show_time(start_dt)} | สิ้นสุด {show_time(end_dt)}) "
        f"({hours:g} ชั่วโมง x {RATE_PER_HOUR:.2f} บาท) "
        f"| สมาชิก: {member_level} | ส่วนลด: {discount_percent:g}% "
        f"| ยอดก่อนลด: {subtotal:.2f} บาท | ยอดสุทธิ: {total:.2f} บาท"
    )
    print("\nตารางการจอง (Booking Records):")
    print("\n".join(build_booking_table_lines()))


def add_computer():
    """เพิ่มเครื่อง (Add computer record) โดยตรง - ตัดเมนูจองเครื่องออกแล้ว"""
    add_computer_record()


def add_computer_record():
    """เพิ่มระเบียนคอมพิวเตอร์ใหม่ พร้อมตรวจสอบ Free Slots"""
    print("\n--- Add New Computer ---")
    comp_id = input_int("Enter CompID (e.g. 1011): ", min_val=1)
    
    records = read_all_computers()
    for r in records:
        if r["comp_id"] == comp_id:
            print(f"[!] Error: CompID {comp_id} มีอยู่ในระบบแล้ว ไม่สามารถเพิ่มซ้ำได้")
            return

    pc_name = input_string("Enter PC Name (e.g. PC-11): ", max_length=15)
    zone_input = input_string(
        "Enter Member (Standard/VIP; vip, vip1, vip2, vip3): ",
        max_length=15,
        choices=["Standard", "VIP", "vip", "vip1", "vip2", "vip3"],
    )
    zone = normalize_computer_zone(zone_input)
    vip_user_id = ask_vip_member() if is_vip_zone(zone) else 0
    ram_gb = input_int("Enter RAM GB (e.g. 16, 32): ", min_val=1)
    rate_per_hr = input_float("Enter Rate (THB/Hour): ", min_val=0.0)
    status = normalize_status(input_string("Enter Status (Available/In Use/Maint): ", max_length=15, choices=["Available", "In Use", "Maint"]))

    use_start = datetime.now().strftime(TIME_FORMAT) if status == "In Use" else ""
    data = pack_computer_record(comp_id, pc_name, zone, ram_gb, rate_per_hr, status, True, use_start, vip_user_id)
    new_record = {"zone": zone, "vip_user_id": vip_user_id, "rate_per_hr": rate_per_hr}
    vip_label, vip_discount, vip_total = computer_vip_cost(new_record, {m["user_id"]: m for m in vip_members()})
    if vip_user_id:
        print(f"[i] สมาชิก VIP: {vip_label} | ส่วนลด {vip_discount:g}% | ราคา {rate_per_hr:.2f} -> สุทธิ {vip_total:.2f} บาท")
    if use_start:
        _, use_end = computer_use_times({"status": status, "rate_per_hr": rate_per_hr, "use_start": use_start})
        print(f"[i] เริ่มใช้งาน {show_time(parse_time(use_start))} | สิ้นสุด {show_time(use_end)}")

    # ค้นหา Slot ที่ถูก Soft Delete เพื่อเขียนทับ (Free List Reuse)
    free_slot_idx = None
    for idx, r in enumerate(records):
        if not r["is_active"]:
            free_slot_idx = idx
            break

    if free_slot_idx is not None:
        with open(FILE_COMPUTERS, "rb+") as f:
            f.seek(free_slot_idx * RECORD_SIZE)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        print(f"[✓] บันทึกข้อมูลคอมพิวเตอร์ใหม่สำเร็จ (เขียนทับ Free Slot ที่ {free_slot_idx})")
    else:
        with open(FILE_COMPUTERS, "ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        print("[✓] บันทึกข้อมูลคอมพิวเตอร์ใหม่ต่อท้ายไฟล์สำเร็จ")

    log_action(f"Added new computer CompID: {comp_id} ({pc_name})")
    generate_report()  # ตาราง 1 / ตาราง 2 ใน summary_report.txt อัปเดตทันที


def update_computer():
    """2) Update แก้ไขระเบียนคอมพิวเตอร์"""
    print("\n--- Update Computer ---")
    comp_id = input_int("Enter CompID to Update: ", min_val=1)
    records = read_all_computers()
    found = False

    with open(FILE_COMPUTERS, "rb+") as f:
        for idx, r in enumerate(records):
            if r["comp_id"] == comp_id and r["is_active"]:
                found = True
                print(f"พบข้อมูลปัจจุบัน -> Name: {r['pc_name']} | Member: {r['zone']} | RAM: {r['ram_gb']} | Rate: {r['rate_per_hr']} | Status: {r['status']}")
                print("(กด Enter หากไม่ต้องการแก้ไขค่านั้นๆ)")

                pc_name_in = input(f"New PC Name [{r['pc_name']}]: ").strip()
                pc_name = pc_name_in if pc_name_in else r['pc_name']

                zone_in = input(f"New Member [{r['zone']}]: ").strip()
                zone = normalize_computer_zone(zone_in) if zone_in else r['zone']
                vip_user_id = ask_vip_member(r["vip_user_id"]) if is_vip_zone(zone) else 0

                ram_in = input(f"New RAM [{r['ram_gb']}]: ").strip()
                ram_gb = int(ram_in) if ram_in.isdigit() else r['ram_gb']

                rate_in = input(f"New Rate [{r['rate_per_hr']}]: ").strip()
                try:
                    rate_per_hr = float(rate_in) if rate_in else r['rate_per_hr']
                except ValueError:
                    rate_per_hr = r['rate_per_hr']

                status_in = input(f"New Status [{r['status']}]: ").strip()
                status = normalize_status(status_in) if status_in else r['status']
                if status not in COMPUTER_STATUSES:
                    print(f"[!] สถานะต้องเป็น Available / In Use / Maint เท่านั้น (คงสถานะเดิม {r['status']})")
                    status = r['status']
                has_open_issue = any(
                    str(item["comp_id"]) == str(comp_id) and item["status"] == "Open"
                    for item in _read_problem_reports())
                if has_open_issue and status != "Maint":
                    print("[!] เครื่องนี้ยังมีรายงานปัญหา Open อยู่ จึงเปลี่ยนเป็น Maint "
                          "(ปิดรายงานเป็น Closed ก่อน เครื่องจึงจะกลับเป็น Available / Active = Yes)")
                    status = "Maint"

                # In Use: เริ่มนับเวลาใหม่เมื่อเพิ่งเปลี่ยนเป็น In Use, ถ้าเป็น In Use อยู่แล้วคงเวลาเริ่มเดิม
                # (เพิ่มเรตราคา = เพิ่มเวลา เวลาสิ้นสุดจะขยายตามเรตใหม่อัตโนมัติ)
                if status != "In Use":
                    use_start = ""
                elif r["status"] == "In Use" and r["use_start"]:
                    use_start = r["use_start"]
                else:
                    use_start = datetime.now().strftime(TIME_FORMAT)
                f.seek(idx * RECORD_SIZE)
                f.write(pack_computer_record(comp_id, pc_name, zone, ram_gb, rate_per_hr, status, True, use_start, vip_user_id))
                f.flush()
                os.fsync(f.fileno())
                print("[✓] แก้ไขข้อมูลคอมพิวเตอร์เรียบร้อยแล้ว")
                if use_start:
                    _, use_end = computer_use_times(
                        {"status": status, "rate_per_hr": rate_per_hr, "use_start": use_start})
                    print(f"[i] เริ่มใช้งาน {show_time(parse_time(use_start))} | สิ้นสุด {show_time(use_end)}")
                log_action(f"Updated computer CompID: {comp_id}")
                break

    if found:
        generate_report()  # ตาราง 1 / ตาราง 2 ใน summary_report.txt อัปเดตทันที
    else:
        print("[!] ไม่พบ CompID นี้ หรือสถานะถูกลบไปแล้ว")


def delete_computer():
    """ลบระเบียนคอมพิวเตอร์และรายการจองที่อ้างถึงเครื่องนั้น"""
    print("\n--- Delete Computer ---")
    comp_id = input_int("Enter CompID to Delete: ", min_val=1)
    records = read_all_computers()
    remaining_records = [record for record in records if record["comp_id"] != comp_id]
    if len(remaining_records) == len(records):
        print("[!] ไม่พบ CompID นี้")
        return

    with open(FILE_COMPUTERS, "wb") as file:
        for record in remaining_records:
            file.write(
                pack_computer_record(
                    record["comp_id"], record["pc_name"], record["zone"],
                    record["ram_gb"], record["rate_per_hr"], record["status"],
                    record["is_active"], record["use_start"], record["vip_user_id"],
                )
            )
        file.flush()
        os.fsync(file.fileno())

    bookings = read_all_bookings()
    remaining_bookings = [booking for booking in bookings if booking["comp_id"] != comp_id]
    if len(remaining_bookings) != len(bookings):
        write_all_bookings(remaining_bookings)

    log_action(f"Deleted computer CompID: {comp_id} and related bookings")
    generate_report()
    print(f"[✓] ลบคอมพิวเตอร์ CompID {comp_id} และรายการจองที่เกี่ยวข้องแล้ว")


def view_menu():
    """4) View เมนูย่อยสำหรับการดูข้อมูล"""
    while True:
        print("\n--- 4) View Sub-Menu ---")
        print("  4.1) ดูรายการเดียว (Single Record)")
        print("  4.2) ดูทั้งหมด (View All)")
        print("  4.3) ดูแบบกรอง (Filter by Member)")
        print("  4.4) สถิติโดยสรุป (Summary Stats)")
        print("  0) ย้อนกลับเมนูก่อนหน้า")
        
        choice = input("เลือกเมนูย่อย (0, 1-4): ").strip()
        records = read_all_computers()

        if choice in ["1", "4.1"]:
            cid = input_int("Enter CompID: ", min_val=1)
            rec = next((r for r in records if r["comp_id"] == cid), None)
            if rec:
                print(f"\nID: {rec['comp_id']} | Name: {rec['pc_name']} | Member: {rec['zone']} | RAM: {rec['ram_gb']}GB | Rate: {rec['rate_per_hr']:.2f} THB | Status: {rec['status']} | Start: {show_time(computer_use_times(rec)[0])} | End: {show_time(computer_use_times(rec)[1])} | Active: {rec['is_active']}")
                log_action(f"Viewed single record CompID: {cid}")
            else:
                print("[!] ไม่พบข้อมูล")

        elif choice in ["2", "4.2"]:
            report_text = generate_report()
            table_part = report_text.split("Summary")[0]
            print("\n" + table_part)
            log_action("Viewed all computer records")

        elif choice in ["3", "4.3"]:
            zone_filter = input_string("กรอก Member ที่ต้องการกรอง (Standard/VIP): ").strip().lower()
            filtered = [r for r in records if r["zone"].lower() == zone_filter and r["is_active"]]
            print(f"\n--- รายการ Member {zone_filter.upper()} (Active) ---")
            if filtered:
                for r in filtered:
                    print(f"ID: {r['comp_id']} | Name: {r['pc_name']} | RAM: {r['ram_gb']}GB | Rate: {r['rate_per_hr']:.2f} | Status: {r['status']} | Start: {show_time(computer_use_times(r)[0])} | End: {show_time(computer_use_times(r)[1])}")
            else:
                print("[!] ไม่พบข้อมูลใน Member นี้")
            log_action(f"Filtered records by zone: {zone_filter}")

        elif choice in ["4", "4.4"]:
            report_text = generate_report()
            summary_part = report_text[report_text.find("Summary"):]
            print("\n" + summary_part)
            log_action("Viewed summary statistics")

        elif choice == "0":
            break
        else:
            print("[!] ตัวเลือกไม่ถูกต้อง กรุณาเลือก 0 ถึง 4")


# ==========================================
# MAIN PROGRAM LOOP
# ==========================================
def build_pc_status_lines(records: list[dict], total_pcs: int | None = None) -> list[str]:
    """สร้างบรรทัดตารางสถานะ PC ทั้งหมด (PC-01 ถึง PC-N โดย N >= 50 ขยายอัตโนมัติ) ใช้ร่วมกันระหว่าง terminal และ summary_report.txt
    - Available = ว่าง (รวมเครื่องที่ยังไม่ได้ลงทะเบียน)  - In Use = ใช้งานอยู่  - Maint = ซ่อม"""
    if total_pcs is None:
        total_pcs = compute_total_pcs(records)
    info_by_pc = build_pc_status_map(records, total_pcs)

    cells = [(f"PC-{n:02d}",) + info_by_pc.get(n, ("Available", "-", "-")) for n in range(1, total_pcs + 1)]

    # แบ่งเป็น 2 ฝั่ง (ซ้าย/ขวา) เพื่อไม่ให้ตารางยาวเกินไป
    half = (total_pcs + 1) // 2
    rows = []
    for i in range(half):
        left = cells[i]
        right = cells[i + half] if i + half < total_pcs else ("", "", "", "")
        rows.append(list(left) + list(right))

    lines = [f"{PC_STATUS_TITLE_PREFIX} {total_pcs} เครื่อง"]
    headers = ["PC", "Status", "Start At", "End At"] * 2
    lines.extend(format_table(headers, rows, min_widths=[5, 9, 16, 16, 5, 9, 16, 16]))

    counts = Counter(cell[1] for cell in cells)
    lines.append(
        f"สรุป: Available {counts.get('Available', 0)} | Booked {counts.get('Booked', 0)} | In Use {counts.get('In Use', 0)} | "
        f"Maint {counts.get('Maint', 0)} | รวม {total_pcs}"
    )
    return lines


def show_pc_status_table(total_pcs: int | None = None) -> None:
    """แสดงตารางสถานะ PC ทั้งหมดบน terminal"""
    print()
    for line in build_pc_status_lines(read_all_computers(), total_pcs):
        print(line)


def computer_management_menu() -> None:
    while True:
        show_pc_status_table()      # ตารางสถานะเครื่อง อยู่บนสุด
        show_computer_record_table()  # ตาราง 2 ยึดตามข้อมูลจาก Add computer record
        print("\nรายชื่อสมาชิก VIP ทั้งหมด (VIP Members):")
        print("\n".join(vip_members_table_lines()))
        print("\nตารางการจอง (Booking Records):")
        print("\n".join(build_booking_table_lines()))
        print("\n--- จองคอมพิวเตอร์ ---")
        print("1) Add computer record (เพิ่มเครื่อง)")
        print("2) Update (แก้ไข)")
        print("3) Delete (ลบ)")
        print("4) View (ดู)")
        print("5) Generate Report (.txt)")
        print("6) Book computer (จองเครื่องล่วงหน้า -> bookings.bin)")
        print("0) ย้อนกลับหน้าหลัก")
        choice = input("เลือกเมนู (0-6): ").strip()

        if choice == "1":
            add_computer()
        elif choice == "2":
            update_computer()
        elif choice == "3":
            delete_computer()
        elif choice == "4":
            view_menu()
        elif choice == "5":
            log_action("Generated computer report manually")
            report = generate_report()
            print(f"[✓] สร้างรายงาน '{FILE_REPORT}' สำเร็จแล้ว")
            print("\n" + report)
        elif choice == "6":
            book_computer()
        elif choice == "0":
            break
        else:
            print("[!] ตัวเลือกไม่ถูกต้อง กรุณาเลือก 0 ถึง 6")


def show_computer_record_table() -> None:
    """ตาราง 2: แสดงตารางเครื่องที่เพิ่มจาก Add computer record (ไม่รวมตารางจอง/Summary)"""
    report_text = generate_report()
    table_part = report_text.split("\n" + PC_STATUS_TITLE_PREFIX)[0]
    # ตัดหัวรายงานด้านบนออก เหลือเฉพาะตารางเครื่อง
    table_start = table_part.find("\n+-") + 1
    print("\nตารางข้อมูลเครื่อง (Computer Records):")
    print(table_part[table_start:].rstrip())


def show_report_tables(report_text: str) -> None:
    """แสดงเฉพาะส่วนตาราง (ตัดส่วน Summary ท้ายรายงานออก) ให้ดูผ่าน terminal ได้ทันที"""
    print("\n" + report_text.split("\nSummary")[0].rstrip())


# ==========================================
# VIEW .BIN FILES + JOIN CHECK (เมนู 4)
# ==========================================
def _bin_file_info(path: str, record_size: int) -> str:
    size = os.path.getsize(path) if os.path.exists(path) else 0
    count = size // record_size if record_size else 0
    return f"File: {path} | Size: {size} bytes | Record size: {record_size} bytes | Records: {count}"


def view_computers_bin() -> None:
    """แสดงข้อมูลดิบใน computers.bin ทุก record (รวม record ที่ถูกลบ/Inactive)"""
    records = read_all_computers()
    print(f"\n=== computers.bin ===")
    print(_bin_file_info(FILE_COMPUTERS, RECORD_SIZE))
    if not records:
        print("- ไม่มีข้อมูลใน computers.bin")
        return
    rows = [[r["comp_id"], r["pc_name"], r["zone"], r["ram_gb"], f"{r['rate_per_hr']:.2f}",
             r["raw_status"], "Yes" if r["is_active"] else "No", r["use_start"] or "-",
             r["vip_user_id"] or "-"] for r in records]
    print("\n".join(format_table(
        ["CompID", "PC Name", "Member", "RAM(GB)", "Rate", "Status", "Active", "Use Start", "VIP User ID (FK)"],
        rows, bottom_border=True)))
    print(f"รวม {len(records)} records")


def view_customers_bin() -> None:
    """แสดงข้อมูลดิบใน customers.bin ทุก record"""
    members = read_member_applications()
    print(f"\n=== customers.bin ===")
    print(_bin_file_info(FILE_CUSTOMERS, MEMBER_APPLICATION_SIZE))
    if not members:
        print("- ไม่มีข้อมูลใน customers.bin")
        return
    rows = [[m["user_id"], m["name"], m["member_level"], f"{member_discount(m['member_level']):g}%",
             m["applied_at"], "Yes" if m["is_active"] else "No"] for m in members]
    print("\n".join(format_table(
        ["User ID (PK)", "Name", "Member Level", "Discount", "Applied At", "Active"],
        rows, bottom_border=True)))
    print(f"รวม {len(members)} records")


def view_bookings_bin() -> None:
    """แสดงข้อมูลดิบใน bookings.bin ทุก record"""
    bookings = read_all_bookings()
    print(f"\n=== bookings.bin ===")
    print(_bin_file_info(FILE_BOOKINGS, BOOKING_SIZE))
    if not bookings:
        print("- ไม่มีข้อมูลใน bookings.bin")
        return
    rows = [[b["booking_id"], b["comp_id"], b["user_id"] or "-", b["pc_name"] or "-",
             b["booking_date"], b["time_slot"], f"{b['hours']:g}", f"{b['discount_percent']:g}%",
             f"{b['total']:.2f}", b["booked_at"], "Yes" if b["is_active"] else "No"] for b in bookings]
    print("\n".join(format_table(
        ["BookID (PK)", "CompID (FK)", "User ID (FK)", "PC Name", "Date", "Time", "Hours",
         "Discount", "Total (THB)", "Booked At", "Active"],
        rows, bottom_border=True)))
    print(f"รวม {len(bookings)} records")


def view_join_check() -> None:
    """ตรวจการ JOIN ระหว่าง 3 ไฟล์ (ให้เทียบกับ report ได้)
    JOIN 1: bookings.comp_id      -> computers.comp_id
    JOIN 2: computers.vip_user_id -> customers.user_id
    JOIN 3: bookings.user_id      -> customers.user_id"""
    computers = read_all_computers()
    members = read_member_applications()
    bookings = read_all_bookings()
    pcs = {r["comp_id"]: r for r in computers}
    mem = {m["user_id"]: m for m in members}

    print("\n=== JOIN CHECK (ความเชื่อมโยงของข้อมูล 3 ไฟล์) ===")

    # JOIN 1
    print("\nJOIN 1: bookings.bin (CompID) -> computers.bin (CompID)")
    rows, ok = [], 0
    for b in bookings:
        rec = pcs.get(b["comp_id"])
        ok += rec is not None
        rows.append([b["booking_id"], b["comp_id"], b["booking_date"], b["time_slot"],
                     rec["pc_name"] if rec else "-", rec["zone"] if rec else "-",
                     "MATCH" if rec else "NOT FOUND"])
    if rows:
        print("\n".join(format_table(
            ["BookID", "CompID", "Date", "Time", "PC Name (computers.bin)", "Member", "Result"],
            rows, bottom_border=True)))
    print(f"-> match {ok}/{len(bookings)}")

    # JOIN 2
    print("\nJOIN 2: computers.bin (VIP User ID) -> customers.bin (User ID)")
    rows, ok, total = [], 0, 0
    for r in computers:
        if not r["vip_user_id"]:
            continue
        total += 1
        m = mem.get(r["vip_user_id"])
        ok += m is not None
        rows.append([r["comp_id"], r["pc_name"], r["vip_user_id"],
                     m["name"] if m else "-", m["member_level"] if m else "-",
                     "MATCH" if m else "NOT FOUND"])
    if rows:
        print("\n".join(format_table(
            ["CompID", "PC Name", "VIP User ID", "Name (customers.bin)", "Member Level", "Result"],
            rows, bottom_border=True)))
    print(f"-> match {ok}/{total}")

    # JOIN 3
    print("\nJOIN 3: bookings.bin (User ID) -> customers.bin (User ID)")
    rows, ok, total = [], 0, 0
    for b in bookings:
        if not b["user_id"]:
            continue
        total += 1
        m = mem.get(b["user_id"])
        ok += m is not None
        rows.append([b["booking_id"], b["user_id"], m["name"] if m else "-",
                     m["member_level"] if m else "-", "MATCH" if m else "NOT FOUND"])
    if rows:
        print("\n".join(format_table(
            ["BookID", "User ID", "Name (customers.bin)", "Member Level", "Result"],
            rows, bottom_border=True)))
    guests = sum(1 for b in bookings if not b["user_id"])
    print(f"-> match {ok}/{total} (การจองแบบ Guest/ไม่ผูกสมาชิก: {guests} รายการ)")


def view_bin_files_menu() -> None:
    """4) ดูข้อมูล .bin ทั้ง 3 ไฟล์ เพื่อเทียบกับ report"""
    while True:
        print("\n--- 4) View .bin Files (ดูข้อมูลไฟล์ไบนารี) ---")
        print("  1) computers.bin")
        print("  2) customers.bin")
        print("  3) bookings.bin")
        print("  4) ดูทั้ง 3 ไฟล์ + Join Check")
        print("  5) Join Check อย่างเดียว")
        print("  0) ย้อนกลับ")
        choice = input("เลือกเมนู (0-5): ").strip()
        if choice == "1":
            view_computers_bin()
            log_action("Viewed computers.bin")
        elif choice == "2":
            view_customers_bin()
            log_action("Viewed customers.bin")
        elif choice == "3":
            view_bookings_bin()
            log_action("Viewed bookings.bin")
        elif choice == "4":
            view_computers_bin()
            view_customers_bin()
            view_bookings_bin()
            view_join_check()
            log_action("Viewed all .bin files with join check")
        elif choice == "5":
            view_join_check()
            log_action("Viewed join check")
        elif choice == "0":
            break
        else:
            print("[!] ตัวเลือกไม่ถูกต้อง กรุณาเลือก 0 ถึง 5")


def main():
    init_data_files()

    while True:
        print("\n==========================================")
        print(" Cyber Cafe Booking System (Python File I/O)")
        print("==========================================")
        print("1) จองคอมพิวเตอร์")
        print("2) จำลองลูกค้ารายงานปัญหา")
        print("3) การสมัครสมาชิก")
        print("4) ดูข้อมูลไฟล์ .bin ทั้ง 3 ไฟล์ (View + Join Check)")
        print("0) ออก")

        choice = input("\nเลือกเมนูหลัก (0-4): ").strip()

        if choice == "1":
            computer_management_menu()
        elif choice == "2":
            show_report_tables(generate_issue_report())
            issue_management_menu(read_all_computers)
        elif choice == "3":
            show_report_tables(generate_member_report())
            member_application_management_menu()
        elif choice == "4":
            view_bin_files_menu()
        elif choice == "0":
            log_action("Exited application safely")
            report = generate_report()
            print(f"\n[✓] สร้างรายงานอัตโนมัติ ({FILE_REPORT}) flush/sync และปิดโปรแกรมเรียบร้อยแล้ว")
            break
        else:
            print("[!] เมนูไม่ถูกต้อง กรุณากรอกตัวเลข 0 ถึง 4")


if __name__ == "__main__":
    main()