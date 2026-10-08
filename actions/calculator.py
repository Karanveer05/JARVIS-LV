"""
calculator — exact arithmetic, unit conversion and live currency conversion.

Language models get long arithmetic wrong; this does not. Expressions are parsed
into an AST and walked by hand — nothing is ever passed to eval(), so a spoken or
injected "expression" cannot run code.
"""
import ast
import json
import math
import operator
import re
import urllib.parse
import urllib.request

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda x: math.sin(math.radians(x)),
          "cos": lambda x: math.cos(math.radians(x)), "tan": lambda x: math.tan(math.radians(x)),
          "log": math.log10, "ln": math.log, "abs": abs, "round": round,
          "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial}
_CONSTS = {"pi": math.pi, "e": math.e}


def _walk(node):
    if isinstance(node, ast.Expression):
        return _walk(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _walk(node.left), _walk(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 1000 and abs(a) > 1:
            raise ValueError("that power is too large to compute")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_walk(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and not node.keywords:
        args = [_walk(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 500:
            raise ValueError("factorial is too large to compute")
        return _FUNCS[node.func.id](*args)
    raise ValueError("unsupported expression")


def _prepare(expr: str) -> str:
    s = expr.strip().lower().replace("×", "*").replace("÷", "/").replace("^", "**")
    s = s.replace(",", "")
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", r"(\1/100*\2)", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    s = re.sub(r"\b(plus)\b", "+", s)
    s = re.sub(r"\b(minus)\b", "-", s)
    s = re.sub(r"\b(times|multiplied by)\b", "*", s)
    s = re.sub(r"\b(divided by|over)\b", "/", s)
    s = re.sub(r"\bto the power of\b", "**", s)
    s = re.sub(r"(\d)\s*\(", r"\1*(", s)
    return s


def calculate(expr: str) -> str:
    try:
        tree = ast.parse(_prepare(expr), mode="eval")
        value = _walk(tree)
    except ZeroDivisionError:
        return "That divides by zero."
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        return f"I could not calculate '{expr}': {e if str(e) else 'invalid expression'}."
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "That has no finite answer."
        value = round(value, 10)
        if value == int(value) and abs(value) < 1e15:
            value = int(value)
    return f"{expr.strip()} = {value:,}" if isinstance(value, int) and abs(value) < 10**15 else f"{expr.strip()} = {value}"


# ── Units ────────────────────────────────────────────────────────────────────
# Each group maps unit -> size in the group's base unit.
_GROUPS = {
    "length": {"m": 1, "meter": 1, "meters": 1, "metre": 1, "metres": 1, "km": 1000, "kilometer": 1000,
               "kilometers": 1000, "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01, "mm": 0.001,
               "millimeter": 0.001, "millimeters": 0.001, "mile": 1609.344, "miles": 1609.344,
               "yard": 0.9144, "yards": 0.9144, "foot": 0.3048, "feet": 0.3048, "ft": 0.3048,
               "inch": 0.0254, "inches": 0.0254, "in": 0.0254},
    "mass": {"kg": 1, "kilogram": 1, "kilograms": 1, "g": 0.001, "gram": 0.001, "grams": 0.001,
             "mg": 1e-6, "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "pounds": 0.45359237,
             "oz": 0.028349523125, "ounce": 0.028349523125, "ounces": 0.028349523125,
             "tonne": 1000, "tonnes": 1000, "ton": 1000},
    "volume": {"l": 1, "liter": 1, "liters": 1, "litre": 1, "litres": 1, "ml": 0.001, "milliliter": 0.001,
               "milliliters": 0.001, "gallon": 3.785411784, "gallons": 3.785411784, "gal": 3.785411784,
               "cup": 0.2365882365, "cups": 0.2365882365, "tbsp": 0.01478676478, "tsp": 0.00492892159},
    "speed": {"m/s": 1, "mps": 1, "km/h": 1 / 3.6, "kmh": 1 / 3.6, "kph": 1 / 3.6,
              "mph": 0.44704, "knot": 0.514444, "knots": 0.514444},
    "data": {"b": 1, "byte": 1, "bytes": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
             "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4},
    "time": {"s": 1, "sec": 1, "seconds": 1, "second": 1, "min": 60, "minute": 60, "minutes": 60,
             "h": 3600, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400,
             "week": 604800, "weeks": 604800},
}
_TEMPS = {"c": "c", "celsius": "c", "°c": "c", "f": "f", "fahrenheit": "f", "°f": "f",
          "k": "k", "kelvin": "k"}


def _to_c(v, u):
    return v if u == "c" else (v - 32) * 5 / 9 if u == "f" else v - 273.15


def _from_c(v, u):
    return v if u == "c" else v * 9 / 5 + 32 if u == "f" else v + 273.15


def _fmt(x: float) -> str:
    if abs(x) >= 1e6 or (0 < abs(x) < 1e-4):
        return f"{x:.6g}"
    s = f"{round(x, 6):,}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def convert_units(value: float, src: str, dst: str) -> str | None:
    a, b = src.strip().lower(), dst.strip().lower()
    if a in _TEMPS and b in _TEMPS:
        out = _from_c(_to_c(value, _TEMPS[a]), _TEMPS[b])
        return f"{_fmt(value)} {src} = {_fmt(out)} {dst}"
    for units in _GROUPS.values():
        if a in units and b in units:
            return f"{_fmt(value)} {src} = {_fmt(value * units[a] / units[b])} {dst}"
    return None


def convert_currency(value: float, src: str, dst: str) -> str:
    a, b = src.strip().upper(), dst.strip().upper()
    if not (re.fullmatch(r"[A-Z]{3}", a) and re.fullmatch(r"[A-Z]{3}", b)):
        return f"I don't know how to convert '{src}' to '{dst}'. Use 3-letter currency codes like USD, INR."
    if a == b:
        return f"{_fmt(value)} {a} = {_fmt(value)} {b}"
    q = urllib.parse.urlencode({"from": a, "to": b, "amount": value})
    try:
        req = urllib.request.Request(f"https://api.frankfurter.dev/v1/latest?{q}",
                                     headers={"User-Agent": "Mozilla/5.0 (JARVIS calculator)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        out = d["rates"][b]
        return f"{_fmt(value)} {a} = {_fmt(out)} {b} (rate of {d.get('date', 'today')})"
    except Exception as e:
        return f"I could not get the exchange rate: {e}"


_CODES = {"usd", "inr", "eur", "gbp", "jpy", "aud", "cad", "chf", "cny", "aed", "sar", "pkr",
          "sgd", "nzd", "zar", "brl", "mxn", "krw", "try", "rub", "sek", "nok", "dkk", "thb",
          "myr", "idr", "php", "hkd", "pln", "czk", "huf", "ils"}


def calculator(parameters: dict, player=None) -> str:
    action = str(parameters.get("action", "")).strip().lower()
    expr = str(parameters.get("expression", "")).strip()
    src = str(parameters.get("from", "")).strip()
    dst = str(parameters.get("to", "")).strip()
    raw_val = parameters.get("value", None)

    if action in ("convert", "conversion", "currency", "unit") or (src and dst):
        try:
            val = float(str(raw_val).replace(",", "")) if raw_val not in (None, "") else None
        except ValueError:
            val = None
        if val is None:
            return "What amount should I convert?"
        if not src or not dst:
            return "Tell me what to convert from and to."
        if src.lower() in _CODES or dst.lower() in _CODES:
            return convert_currency(val, src, dst)
        r = convert_units(val, src, dst)
        return r or f"I can't convert {src} to {dst} — they are different kinds of unit or unknown to me."
    if not expr:
        return "Give me an expression to calculate, or something to convert."
    return calculate(expr)


TOOL = {
    "name": "calculator",
    "description": (
        "Exact maths: arithmetic, percentages ('15% of 240'), powers, roots, trig "
        "(degrees), and conversions — units (km to miles, kg to lb, °C to °F, GB to "
        "MB) and live currency (USD to INR). ALWAYS use this for any calculation "
        "instead of doing it in your head."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "calculate | convert (default: calculate)"},
            "expression": {"type": "STRING", "description": "For calculate, e.g. '(12.5*8)+15% of 200'"},
            "value": {"type": "STRING", "description": "For convert: the amount"},
            "from": {"type": "STRING", "description": "For convert: source unit or currency code"},
            "to": {"type": "STRING", "description": "For convert: target unit or currency code"},
        },
        "required": [],
    },
    "handler": calculator,
}
