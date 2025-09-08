from flask import Flask, render_template, request, jsonify
from decimal import Decimal, getcontext, ROUND_HALF_UP
import math
from datetime import datetime, date
import calendar

getcontext().prec = 28

app = Flask(__name__)

# Helper: round to 2 decimals as monetary

def months_to_years_months(total_months):
    """Convert integer months to (years, months)."""
    years = total_months // 12
    months = total_months % 12
    return int(years), int(months)

def add_months_to_date(dt, months):
    """
    Add 'months' months to date dt (dt is a datetime.date or datetime).
    If resulting month has fewer days than dt.day, clamp to last day of month.
    """
    if isinstance(dt, datetime):
        dt_date = dt.date()
    else:
        dt_date = dt
    y = dt_date.year + (months // 12)
    m = dt_date.month + (months % 12)
    if m > 12:
        y += 1
        m -= 12
    # clamp day
    last_day = calendar.monthrange(y, m)[1]
    day = min(dt_date.day, last_day)
    return date(y, m, day)


def money(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def monthly_rate(annual_pct):
    return Decimal(annual_pct) / Decimal(12 * 100)

def calc_standard_emi(principal, annual_rate_pct, months):
    P = Decimal(principal)
    r = monthly_rate(annual_rate_pct)
    n = int(months)
    if r == 0:
        return money(P / n)
    numerator = r * P
    denominator = (1 - (1 + r) ** (-n))
    emi = numerator / denominator
    return money(emi)


def simulate_schedule(principal, annual_rate_pct, months, starting_emi=None):
    """
    Simulate an amortization schedule (no prepayment) and return totals
    """
    P = Decimal(principal)
    r = monthly_rate(annual_rate_pct)
    n = int(months)
    emi = starting_emi or calc_standard_emi(P, annual_rate_pct, n)
    emi = Decimal(emi)
    month = 0
    interest_total = Decimal('0')
    remaining = Decimal(P)
    schedule = []
    while remaining > Decimal('0.005') and month < 1000:
        month += 1
        interest = (remaining * r)
        principal_component = emi - interest
        if principal_component <= 0:
            raise ValueError("EMI does not cover the interest; increase EMI or check rates.")
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
        if month >= n and remaining > 0:
            # safety: if we've gone full term but small remaining due to rounding, finish
            pass
    return {
        'schedule': schedule,
        'months': month,
        'total_interest': money(interest_total),
        'total_paid': money(sum([s['emi'] for s in schedule]))
    }

def is_prepay_event(month_index, start_after_months, frequency):
    """
    month_index: 1-based month number (1 = first month)
    start_after_months: prepay start offset in months (0 means prepay may start at month 1)
    frequency: 'monthly','quarterly','half-yearly','yearly','once'
    """
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
        # Only on the first eligible month
        return offset_index == 1
    return False

def simulate_with_prepay(principal, annual_rate_pct, months,
                         base_emi=None,
                         extra_emi_amount=0,
                         lump_sum_amount=0,
                         emi_increase_pct_per_year=0,
                         start_after_months=0,
                         frequency='monthly'):
    """
    Simulate schedule honoring prepayment options.
    Returns schedule and totals.
    """
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
    # anniversary detection: when month % 12 == 1 after start -> we consider start month as month 1
    while remaining > Decimal('0.005') and month < 1200:
        month += 1
        # At start of month, check for EMI increase on anniversaries
        # Increase EMI on months 13,25,...? We define that EMI increase triggers at months where (month-1) % 12 == 0 and month != 1?
        # For "Increase per year", commonly increase is applied at yearly anniversaries after the first year.
        if month > 1 and (month - 1) % 12 == 0 and inc_pct != 0:
            emi = (emi * (Decimal(1) + inc_pct)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # compute interest
        interest = remaining * r
        principal_component = emi - interest
        if principal_component <= 0:
            raise ValueError("EMI too small to cover interest; adjust EMI or increase rate.")
        # If EMI principal > remaining, final payment
        if principal_component >= remaining:
            principal_component = remaining
            emi_for_month = interest + principal_component
        else:
            emi_for_month = emi

        # apply normal payment
        remaining -= principal_component
        interest_total += interest

        # Apply any prepay event scheduled for this month
        if is_prepay_event(month, start_after_months, frequency):
            # Lump-sum first (if any)
            if lump > 0:
                pay = min(lump, remaining)
                remaining -= pay
                schedule_prepay = money(pay)
                lump = Decimal('0') if frequency == 'once' else lump
            else:
                schedule_prepay = None
            # Extra EMI: applied as principal payment (extra towards principal)
            if extra > 0:
                pay_extra = min(extra, remaining)
                remaining -= pay_extra
                schedule_extra = money(pay_extra)
                if frequency == 'once':
                    extra = Decimal('0')
            else:
                schedule_extra = None
        else:
            schedule_prepay = None
            schedule_extra = None

        schedule.append({
            'month': month,
            'emi': money(emi_for_month),
            'interest': money(interest),
            'principal': money(principal_component),
            'extra_paid': schedule_extra,
            'lump_paid': schedule_prepay,
            'remaining': money(max(remaining, Decimal('0.00')))
        })
    return {
        'schedule': schedule,
        'months': month,
        'total_interest': money(interest_total),
        'total_paid': money(sum([s['emi'] for s in schedule]) + sum([s.get('extra_paid') or 0 for s in schedule]) + sum([s.get('lump_paid') or 0 for s in schedule]))
    }

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("index.html", form={})
    # POST: collect form
    form = request.form
    principal = Decimal(form.get("principal", "0").replace(',', ''))
    loan_start = form.get("loan_start")  # string date like YYYY-MM-DD
    tenure_years = Decimal(form.get("tenure_years", "0"))
    roi = Decimal(form.get("roi", "0"))
    # prepay fields
    method = form.get("method", "none")
    extra_emi = Decimal(form.get("extra_emi", "0") or "0")
    lump_sum = Decimal(form.get("lump_sum", "0") or "0")
    emi_increase_pct = Decimal(form.get("emi_increase_pct", "0") or "0")
    start_type = form.get("start_type", "immediate")
    start_after_value = int(form.get("start_after_value", "0") or 0)
    start_after_months = 0
    if start_type == "immediate":
        start_after_months = 0
    elif start_type == "after_months":
        start_after_months = start_after_value
    elif start_type == "after_years":
        start_after_months = start_after_value * 12
    frequency = form.get("frequency", "monthly")

    total_months = int(tenure_years * 12)

    # Baseline
    baseline = simulate_schedule(principal, roi, total_months)

    # Decide parameters for prepay simulation based on method
    extra_amt = Decimal('0')
    lump_amt = Decimal('0')
    inc_pct = Decimal('0')
    if method == "extra_emi":
        extra_amt = extra_emi
    elif method == "lump":
        lump_amt = lump_sum
    elif method == "increase_emi":
        inc_pct = emi_increase_pct
    elif method == "extra_and_increase":
        extra_amt = extra_emi
        inc_pct = emi_increase_pct
    # Use base EMI as baseline EMI
    base_emi = calc_standard_emi(principal, roi, total_months)

    with_prepay = simulate_with_prepay(
        principal=principal,
        annual_rate_pct=roi,
        months=total_months,
        base_emi=base_emi,
        extra_emi_amount=extra_amt,
        lump_sum_amount=lump_amt,
        emi_increase_pct_per_year=inc_pct,
        start_after_months=start_after_months,
        frequency=frequency
    )

    result = {
        'baseline_total_interest': str(baseline['total_interest']),
        'baseline_months': baseline['months'],
        'with_prepay_total_interest': str(with_prepay['total_interest']),
        'with_prepay_months': with_prepay['months'],
        'interest_saved': str(money(Decimal(baseline['total_interest']) - Decimal(with_prepay['total_interest']))),
        'months_saved': baseline['months'] - with_prepay['months'],
    }
    # --- NEW: convert months -> years + months
    yp, mp = months_to_years_months(with_prepay['months'])
    result['with_prepay_years'] = yp
    result['with_prepay_months_rem'] = mp  # remaining months after years

    # If user provided a loan start date, compute end date
    loan_start_str = form.get("loan_start")
    if loan_start_str:
        try:
            # accept YYYY-MM-DD
            loan_start_dt = datetime.strptime(loan_start_str, "%Y-%m-%d").date()
            # assuming with_prepay['months'] counts months including the first month,
            # the end date is start + (months - 1) months, because start month counts as month 1.
            months_to_add = with_prepay['months'] - 1 if with_prepay['months'] > 0 else 0
            end_date = add_months_to_date(loan_start_dt, months_to_add)
            result['with_prepay_end_date'] = end_date.strftime("%d %b %Y")
        except Exception:
            # if parsing fails, skip end date
            result['with_prepay_end_date'] = None
    else:
        result['with_prepay_end_date'] = None
    # For UI we return the top-level result and small slices of schedules
    return render_template("index.html", result=result,
                           baseline=baseline, with_prepay=with_prepay,
                           form=form)

if __name__ == "__main__":
    app.run(debug=True)
