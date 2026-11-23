"""Canonical recovery identifiers shared by account creation, login and recovery."""
import re
import phonenumbers


def normalize_email(value):
    if not value or not value.strip():
        return None
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('Enter a valid email address')
    return value


def normalize_phone(value, country='NZ'):
    if not value or not value.strip():
        return None
    try:
        number = phonenumbers.parse(value.strip(), country.upper())
        if number.extension or not phonenumbers.is_valid_number(number):
            raise ValueError('Enter a valid phone number and country')
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException as exc:
        raise ValueError('Enter a valid phone number and country') from exc


def countries():
    return [{'iso': iso, 'dial_code': '+' + str(phonenumbers.country_code_for_region(iso))}
            for iso in sorted(phonenumbers.SUPPORTED_REGIONS)]
