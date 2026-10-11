"""Contacts: reading what phones and services export, writing vCards every app can import."""

import unittest

import helpers  # noqa: F401
from ddrive import contacts

IPHONE = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//Apple Inc.//iPhone OS 17.0//EN\r\n"
    "N:Appleseed;Johnny;;;\r\nFN:Johnny Appleseed\r\nORG:Apple Inc.;\r\n"
    "item1.EMAIL;type=INTERNET;type=pref:johnny@example.com\r\n"
    "TEL;type=CELL;type=VOICE;type=pref:+1 (555) 123-4567\r\n"
    "item2.ADR;type=HOME;type=pref:;;1 Infinite Loop;Cupertino;CA;95014;United States\r\n"
    "BDAY:1990-04-01\r\nNOTE:Met at the conference\\, 2024\r\n"
    "X-SOCIALPROFILE;type=twitter:x.com/johnny\r\n"
    "END:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A very long name that goes on and on so the line has to be fo\r\n lded\r\n"
    "TEL:555 0000\r\nEND:VCARD\r\n")

ANDROID_21 = (
    "BEGIN:VCARD\nVERSION:2.1\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCrgen;;;\n"
    "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:J=C3=BCrgen M=C3=BCller\n"
    "TEL;CELL;PREF:+49 151 2345678\nTEL;WORK:+49 30 123456\nEND:VCARD\n")

GOOGLE_CSV = (
    "First Name,Middle Name,Last Name,Organization Name,Organization Title,Birthday,Notes,"
    "E-mail 1 - Label,E-mail 1 - Value,Phone 1 - Label,Phone 1 - Value,Phone 2 - Label,Phone 2 - Value,"
    "Address 1 - Label,Address 1 - Formatted\n"
    "Ada,,Lovelace,Analytical Engines,Programmer,1815-12-10,First programmer,* Home,ada@example.org,"
    "Mobile,+44 7700 900000,Work,+44 20 7946 0000,Home,\"12 St James's Square\nLondon\"\n")

OUTLOOK_CSV = (
    "First Name,Last Name,Company,Job Title,E-mail Address,Mobile Phone,Business Phone,Notes\n"
    "Grace,Hopper,US Navy,Rear Admiral,grace@example.mil,555-1000,555-2000,Found a bug\n")


class ContactsTest(unittest.TestCase):
    def test_iphone_vcard(self):
        a, b = contacts.parse_vcards(IPHONE)
        self.assertEqual(a["name"], "Johnny Appleseed")
        self.assertEqual((a["first"], a["last"], a["org"]), ("Johnny", "Appleseed", "Apple Inc."))
        self.assertEqual(a["phones"], [{"label": "mobile", "value": "+1 (555) 123-4567"}])
        self.assertEqual(a["emails"][0]["value"], "johnny@example.com")
        self.assertEqual(a["addresses"][0]["city"], "Cupertino")
        self.assertEqual(a["bday"], "1990-04-01")
        self.assertEqual(a["note"], "Met at the conference, 2024")
        self.assertTrue(any(l.startswith("X-SOCIALPROFILE") for l in a["extra"]))   # kept as it was
        self.assertEqual(b["name"], "A very long name that goes on and on so the line has to be folded")

    def test_android_quoted_printable(self):
        (c,) = contacts.parse_vcards(ANDROID_21)
        self.assertEqual(c["name"], "Jürgen Müller")
        self.assertEqual([p["label"] for p in c["phones"]], ["mobile", "work"])

    def test_google_and_outlook_csv(self):
        (ada,) = contacts.parse_any(GOOGLE_CSV, "contacts.csv")
        self.assertEqual(ada["name"], "Ada Lovelace")
        self.assertEqual([p["label"] for p in ada["phones"]], ["mobile", "work"])
        self.assertEqual(ada["emails"][0], {"label": "home", "value": "ada@example.org"})
        self.assertIn("London", ada["addresses"][0]["street"])
        (grace,) = contacts.parse_any(OUTLOOK_CSV, "outlook.csv")
        self.assertEqual((grace["name"], grace["org"]), ("Grace Hopper", "US Navy"))
        self.assertEqual({p["label"] for p in grace["phones"]}, {"mobile", "work"})

    def test_round_trip(self):
        for c in contacts.parse_vcards(IPHONE) + contacts.parse_vcards(ANDROID_21) + contacts.parse_any(GOOGLE_CSV):
            text = contacts.to_vcard(c)
            self.assertTrue(all(len(line.encode()) <= 75 for line in text.split("\r\n")))
            (back,) = contacts.parse_vcards(text)
            for k in ("name", "first", "last", "org", "bday", "note", "uid"):
                self.assertEqual(back[k], c[k], k)
            self.assertEqual([p["value"] for p in back["phones"]], [p["value"] for p in c["phones"]])
            self.assertEqual(back["extra"], c["extra"])

    def test_duplicates(self):
        a = {"name": "Jo", "phones": [{"value": "+1 555 123 4567"}], "emails": []}
        b = {"name": "jo", "phones": [{"value": "(555) 123-4567"}], "emails": []}
        c = {"name": "Jo", "phones": [{"value": "999"}], "emails": []}
        self.assertTrue(contacts.same_person(a, b))
        self.assertFalse(contacts.same_person(a, c))


if __name__ == "__main__":
    unittest.main()
