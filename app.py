from flask import Flask, render_template, request
from decimal import Decimal, getcontext, ROUND_HALF_UP
from datetime import datetime, date
import calendar
import json

getcontext().prec = 28

app = Flask(__name__)

# ----------------------------
# Helpers (money, EMI, months)
# ----------------------------
def money(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def format_money_for_display(x):
    """Return a string with commas and two decimals. x may be Decimal, float or str."""
    if x is None or x == "":
        return ""
    d = Decimal(x)
    d = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:,.2f}"

def monthly_rate(annual_pct):
    return Decimal(annual_pct) / Decimal(12 * 100)

def calc_standard_emi(principal, annual_rate_pct, months):
    P = Decimal(principal)
    r = monthly_rate(annual_rate_pct)
    n = int(months)
    if n <= 0:
        return Decimal('0.00')
    if r == 0:
        return money(P / n)
    numerator = r * P
    denominator = (1 - (1 + r) ** (-n))
    emi = numerator / denominator
    return money(emi)

def months_to_years_months(total_months):
    years = total_months // 12
    months = total_months % 12
    return int(years), int(months)

def add_months_to_date(dt, months):
    if isinstance(dt, datetime):
        dt_date = dt.date()
    else:
        dt_date = dt
    y = dt_date.year + (months // 12)
    m = dt_date.month + (months % 12)
    if m > 12:
        y += 1
        m -= 12
    last_day = calendar.monthrange(y, m)[1]
    day = min(dt_date.day, last_day)
    return date(y, m, day)

# ----------------------------
# Input parsing helper
# ----------------------------
def parse_decimal_from_form(value, default="0"):
    """
    Clean commas/whitespace from a form input and convert to Decimal.
    If input is empty/blank -> returns None.
    If invalid -> returns Decimal(default).
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    s = str(value).strip().replace(',', '')
    if s == '':
        return None
    try:
        return Decimal(s)
    except Exception:
        # fallback to default decimal value (as Decimal)
        return Decimal(default)


def parse_int_from_form(value, default=0):
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default

# ----------------------------
# Amortization and prepay sim
# ----------------------------
def simulate_schedule(principal, annual_rate_pct, months, starting_emi=None):
    P = Decimal(principal)
    r = monthly_rate(annual_rate_pct)
    n = int(months)
    emi = Decimal(starting_emi or calc_standard_emi(P, annual_rate_pct, n))
    month = 0
    interest_total = Decimal('0')
    remaining = Decimal(P)
    schedule = []
    while remaining > Decimal('0.005') and month < 2000:
        month += 1
        interest = remaining * r
        principal_component = emi - interest
        if principal_component <= 0:
            raise ValueError("EMI does not cover interest; check inputs.")
        if principal_component > remaining:
            principal_component = remaining
            emi_for_month = interest + principal_component
        else:
            emi_for_month = emi
        remaining -= principal_component
        interest_total += interest
        schedule.append({
            'month': month,
            'emi': money(emi_for_month),
            'interest': money(interest),
            'principal': money(principal_component),
            'remaining': money(max(remaining, Decimal('0.00')))
        })
    return {
        'schedule': schedule,
        'months': month,
        'total_interest': money(interest_total),
        'total_paid': money(sum([s['emi'] for s in schedule]))
    }

def is_prepay_event(month_index, start_after_months, frequency):
    if month_index < 1 + start_after_months:
        return False
    offset_index = month_index - start_after_months
    if frequency == 'monthly':
        return True
    if frequency == 'quarterly':
        return (offset_index - 1) % 3 == 0
    if frequency == 'half-yearly':
        return (offset_index - 1) % 6 == 0
    if frequency == 'yearly':
        return (offset_index - 1) % 12 == 0
    if frequency == 'once':
        return offset_index == 1
    return False

def simulate_with_prepay(principal, annual_rate_pct, months,
                         base_emi=None,
                         extra_emi_amount=0,
                         lump_sum_amount=0,
                         emi_increase_pct_per_year=0,
                         start_after_months=0,
                         frequency='monthly'):
    P = Decimal(principal)
    r = monthly_rate(annual_rate_pct)
    n = int(months)
    emi = Decimal(base_emi or calc_standard_emi(P, annual_rate_pct, n))
    extra = Decimal(extra_emi_amount)
    lump = Decimal(lump_sum_amount)
    inc_pct = Decimal(emi_increase_pct_per_year) / Decimal(100)
    month = 0
    interest_total = Decimal('0')
    remaining = Decimal(P)
    schedule = []
    while remaining > Decimal('0.005') and month < 2000:
        month += 1
        # annual EMI increase applied at every yearly anniversary after month 1:
        if month > 1 and (month - 1) % 12 == 0 and inc_pct != 0:
            emi = (emi * (Decimal(1) + inc_pct)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        interest = remaining * r
        principal_component = emi - interest
        if principal_component <= 0:
            raise ValueError("EMI too small to cover interest.")
        if principal_component >= remaining:
            principal_component = remaining
            emi_for_month = interest + principal_component
        else:
            emi_for_month = emi
        remaining -= principal_component
        interest_total += interest
        schedule_prepay = None
        schedule_extra = None
        if is_prepay_event(month, start_after_months, frequency):
            if lump > 0:
                pay = min(lump, remaining)
                remaining -= pay
                schedule_prepay = money(pay)
                if frequency == 'once':
                    lump = Decimal('0')
            if extra > 0:
                pay_extra = min(extra, remaining)
                remaining -= pay_extra
                schedule_extra = money(pay_extra)
                if frequency == 'once':
                    extra = Decimal('0')
        schedule.append({
            'month': month,
            'emi': money(emi_for_month),
            'interest': money(interest),
            'principal': money(principal_component),
            'extra_paid': schedule_extra,
            'lump_paid': schedule_prepay,
            'remaining': money(max(remaining, Decimal('0.00')))
        })
    total_paid = sum([s['emi'] for s in schedule]) + sum([s.get('extra_paid') or Decimal('0') for s in schedule]) + sum([s.get('lump_paid') or Decimal('0') for s in schedule])
    return {
        'schedule': schedule,
        'months': month,
        'total_interest': money(interest_total),
        'total_paid': money(total_paid)
    }

# ----------------------------
# Routes
# ----------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    # on GET: safe defaults
    if request.method == 'GET':
        return render_template('index.html',
                               form={},
                               current_emi_server=None,
                               schedule_serializable=[],
                               schedule_json="[]",
                               baseline_chart_json="[]",
                               with_prepay_chart_json="[]",
                               loan_start_str=None,
                               result=None,
                               with_prepay=None,
                               baseline=None)

    form = request.form

    # Read & parse inputs safely (strip commas)
    loan_amount = parse_decimal_from_form(form.get('loan_amount'), default="")
    outstanding_principal = parse_decimal_from_form(form.get('principal'), default="0")
    loan_start = form.get('loan_start') or None
    tenure_years = parse_decimal_from_form(form.get('tenure_years'), default="0")
    roi = parse_decimal_from_form(form.get('roi'), default="0")

    # optional prepay fields (parse & clean)
    method = form.get('method') or 'none'
    extra_emi = parse_decimal_from_form(form.get('extra_emi'), default="0")
    lump_sum = parse_decimal_from_form(form.get('lump_sum'), default="0")
    emi_increase_pct = parse_decimal_from_form(form.get('emi_increase_pct'), default="0")
    start_type = form.get('start_type') or 'immediate'
    start_after_value = parse_int_from_form(form.get('start_after_value'), default=0)
    if start_type == 'immediate':
        start_after_months = 0
    elif start_type == 'after_months':
        start_after_months = start_after_value
    elif start_type == 'after_years':
        start_after_months = start_after_value * 12
    else:
        start_after_months = 0
    frequency = form.get('frequency') or 'monthly'

    # convert tenure to int months
    total_months = int(Decimal(tenure_years) * 12) if tenure_years else 0

    # Baseline schedule (simulate using outstanding principal)
    baseline = simulate_schedule(outstanding_principal, roi, total_months)

    # Base EMI for prepay sim: prefer loan_amount if provided and >0, else outstanding principal
    if loan_amount is not None:
        try:
            if loan_amount > 0:
                base_emi = calc_standard_emi(loan_amount, roi, total_months)
            else:
                base_emi = calc_standard_emi(outstanding_principal, roi, total_months)
        except Exception:
            base_emi = calc_standard_emi(outstanding_principal, roi, total_months)
    else:
        base_emi = calc_standard_emi(outstanding_principal, roi, total_months)

    # map method to params
    extra_amt = Decimal('0')
    lump_amt = Decimal('0')
    inc_pct = Decimal('0')
    if method == 'extra_emi':
        extra_amt = extra_emi
    elif method == 'lump':
        lump_amt = lump_sum
    elif method == 'increase_emi':
        inc_pct = emi_increase_pct
    elif method == 'extra_and_increase':
        extra_amt = extra_emi
        inc_pct = emi_increase_pct

    with_prepay = simulate_with_prepay(
        principal=outstanding_principal,
        annual_rate_pct=roi,
        months=total_months,
        base_emi=base_emi,
        extra_emi_amount=extra_amt,
        lump_sum_amount=lump_amt,
        emi_increase_pct_per_year=inc_pct,
        start_after_months=start_after_months,
        frequency=frequency
    )

    # compute savings
    interest_saved = baseline['total_interest'] - with_prepay['total_interest']
    months_saved = baseline['months'] - with_prepay['months']
    yp, mp = months_to_years_months(with_prepay['months'])

    # end date if start given
    if loan_start:
        try:
            loan_start_dt = datetime.strptime(loan_start, "%Y-%m-%d").date()
            months_to_add = with_prepay['months'] - 1 if with_prepay['months'] > 0 else 0
            end_date = add_months_to_date(loan_start_dt, months_to_add)
            end_date_str = end_date.strftime("%d %b %Y")
        except Exception:
            end_date_str = None
    else:
        end_date_str = None

    # Total Property Ownership = outstanding principal + total interest over loan
    baseline_total_property = baseline['total_interest'] + Decimal(outstanding_principal)
    with_prepay_total_property = with_prepay['total_interest'] + Decimal(outstanding_principal)

    # Prepare schedule serializable (strings formatted) for table and CSV
    def schedule_to_serializable(sched):
        out = []
        for r in sched:
            out.append({
                'month': r['month'],
                'emi': format_money_for_display(r['emi']),
                'interest': format_money_for_display(r['interest']),
                'principal': format_money_for_display(r['principal']),
                'extra_paid': format_money_for_display(r['extra_paid']) if r.get('extra_paid') else '',
                'lump_paid': format_money_for_display(r['lump_paid']) if r.get('lump_paid') else '',
                'remaining': format_money_for_display(r['remaining'])
            })
        return out

    schedule_serializable = schedule_to_serializable(with_prepay['schedule'])
    schedule_json = json.dumps(schedule_serializable)

    # Prepare chart series (month -> numeric remaining)
    def schedule_for_chart_numeric(sched):
        lst = []
        for r in sched:
            lst.append({'month': r['month'], 'remaining': float(r['remaining'])})
        return lst

    baseline_for_chart = schedule_for_chart_numeric(baseline['schedule'])
    with_prepay_for_chart = schedule_for_chart_numeric(with_prepay['schedule'])
    baseline_chart_json = json.dumps(baseline_for_chart)
    with_prepay_chart_json = json.dumps(with_prepay_for_chart)

    # server-side current EMI (from loan amount if provided)
    current_emi_server = None
    if loan_amount and total_months > 0:
        try:
            current_emi_server = format_money_for_display(calc_standard_emi(loan_amount, roi, total_months))
        except Exception:
            current_emi_server = None

    # prepare result display values
    result = {
        'baseline_total_interest': format_money_for_display(baseline['total_interest']),
        'baseline_months': baseline['months'],
        'baseline_total_property': format_money_for_display(baseline_total_property),

        'with_prepay_total_interest': format_money_for_display(with_prepay['total_interest']),
        'with_prepay_months': with_prepay['months'],
        'with_prepay_years': yp,
        'with_prepay_months_rem': mp,
        'with_prepay_end_date': end_date_str,
        'with_prepay_total_property': format_money_for_display(with_prepay_total_property),

        'interest_saved': format_money_for_display(interest_saved),
        'months_saved': months_saved
    }

    return render_template(
        'index.html',
        form=form,
        current_emi_server=current_emi_server,
        result=result,
        baseline=baseline,
        with_prepay=with_prepay,
        schedule_serializable=schedule_serializable,
        schedule_json=schedule_json,
        baseline_chart_json=baseline_chart_json,
        with_prepay_chart_json=with_prepay_chart_json,
        loan_start_str=loan_start
    )

if __name__ == '__main__':
    app.run(debug=True)
