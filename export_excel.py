from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule

OUTREACH_COLUMNS = (
    ("Name", "name"),
    ("Headline", "headline"),
    ("Profile URL", "url"),
    ("Organisation type", "org_type"),
    ("Organisation name", "org_name"),
    ("Status hint", "status_hint"),
    ("Years", "years_text"),
    ("Verified status", "verified_status"),
    ("Verified note", "verified_note"),
    ("Outreach status", "status"),
    ("Personal note", "personal_note"),
    ("Draft", "draft"),
    ("Created", "created_at"),
    ("Updated", "updated_at"),
    ("Batch", "batch_name"),
    ("Instagram handle", "instagram_handle"),
    ("Confirmed Instagram link", "confirmed_instagram_link"),
    ("Confirmed YouTube link", "confirmed_youtube_link"),
    ("Source", "source"),
    ("Referred by", "referred_by"),
    ("Touch count", "touch_count"),
    ("Follow-up date", "follow_up_date"),
    ("Sent at", "sent_at"),
    ("Last sent message", "last_sent_text"),
)
OUTREACH_STATUSES = ("new", "shortlisted", "sent", "connected", "replied", "agreed", "rejected")
VERIFIED_STATUSES = ("unverified", "current", "past", "unsure")
FORMULA_PREFIXES = ("=", "+", "-", "@")


def escape_excel_value(value):
    if isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return escape_excel_value(str(value))


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return escape_excel_value(str(value))


def build_workbook(people, export_date=None):
    workbook = Workbook()
    outreach = workbook.active
    outreach.title = "Outreach"
    outreach.append([header for header, _ in OUTREACH_COLUMNS])
    header_fill = PatternFill("solid", fgColor="17684D")
    for cell in outreach[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    outreach.freeze_panes = "A2"
    outreach.auto_filter.ref = f"A1:X{max(1, len(people) + 1)}"

    for person in people:
        row_values = []
        for header, field in OUTREACH_COLUMNS:
            value = person[field]
            if field in ("created_at", "updated_at", "sent_at"):
                value = parse_datetime(value)
            elif field == "follow_up_date":
                value = parse_date(value)
            else:
                value = escape_excel_value(value)
            row_values.append(value)
        outreach.append(row_values)
        row_number = outreach.max_row
        url_cell = outreach.cell(row=row_number, column=3)
        if isinstance(person["url"], str) and person["url"].startswith(
            ("https://", "http://")
        ):
            url_cell.hyperlink = person["url"]
            url_cell.style = "Hyperlink"
        for column in (9, 11, 12, 24):
            outreach.cell(row=row_number, column=column).alignment = Alignment(
                vertical="top", wrap_text=True
            )
        for column in (13, 14, 23):
            cell = outreach.cell(row=row_number, column=column)
            if isinstance(cell.value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"

    widths = {
        "A": 24, "B": 32, "C": 42, "D": 18, "E": 30, "F": 16, "G": 18,
        "H": 18, "I": 38, "J": 20, "K": 38, "L": 48, "M": 20, "N": 20,
        "O": 28, "P": 22, "Q": 42, "R": 42, "S": 18, "T": 24, "U": 14,
        "V": 18, "W": 20, "X": 48,
    }
    for column, width in widths.items():
        outreach.column_dimensions[column].width = width
    outreach.row_dimensions[1].height = 30

    max_data_row = max(2, len(people) + 1)
    outreach_validation = DataValidation(
        type="list", formula1='"' + ",".join(OUTREACH_STATUSES) + '"'
    )
    verified_validation = DataValidation(
        type="list", formula1='"' + ",".join(VERIFIED_STATUSES) + '"'
    )
    outreach.add_data_validation(outreach_validation)
    outreach.add_data_validation(verified_validation)
    outreach_validation.add(f"J2:J{max_data_row}")
    verified_validation.add(f"H2:H{max_data_row}")

    status_fills = {
        "sent": "FFF2CC",
        "replied": "DDEBF7",
        "agreed": "E2F0D9",
        "rejected": "E7E6E6",
    }
    for status, color in status_fills.items():
        outreach.conditional_formatting.add(
            f"J2:J{max_data_row}",
            FormulaRule(
                formula=[f'J2="{status}"'],
                fill=PatternFill("solid", fgColor=color),
            ),
        )

    summary = workbook.create_sheet("Summary")
    summary.append(["Metric", "Value"])
    for cell in summary[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    summary.append(["Total people", "=COUNTA(Outreach!A2:A1048576)"])
    summary.append(["", ""])
    summary.append(["Outreach status", "Count"])
    for cell in summary[4]:
        cell.font = Font(bold=True)
    for status in OUTREACH_STATUSES:
        summary.append(
            [status.title(), f'=COUNTIF(Outreach!J:J,"{status}")']
        )
    verified_start = summary.max_row + 2
    summary.cell(row=verified_start, column=1, value="Verified status").font = Font(
        bold=True
    )
    summary.cell(row=verified_start, column=2, value="Count").font = Font(bold=True)
    for offset, status in enumerate(VERIFIED_STATUSES, start=1):
        summary.append(
            [status.title(), f'=COUNTIF(Outreach!H:H,"{status}")']
        )
    summary.append(["", ""])
    reply_rate_row = summary.max_row + 1
    summary.append(["Reply rate", '=IFERROR(COUNTIF(Outreach!J:J,"replied")/COUNTIF(Outreach!J:J,"sent"),0)'])
    agreement_rate_row = summary.max_row + 1
    summary.append(["Agreement rate", '=IFERROR(COUNTIF(Outreach!J:J,"agreed")/COUNTIF(Outreach!J:J,"sent"),0)'])
    summary.append(["Export date", export_date or datetime.now().date()])
    summary.cell(row=reply_rate_row, column=2).number_format = "0.0%"
    summary.cell(row=agreement_rate_row, column=2).number_format = "0.0%"
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 24
    summary.freeze_panes = "A2"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
