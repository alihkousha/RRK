from parsing.entities import ad_mentions, extract_mentions, normalize, valid_code

# Shaped like real notices (Arabic ي/ك as the gazette writes them); names and numbers are made up.
CHANGES = ("آگهي تغييرات شركت نمونه سازان شرق شركت سهامي خاص به شماره ثبت 151 و شناسه ملي 10200000001"
           " به استناد صورتجلسه مجمع عمومي عادي مورخ 21/02/1396 تصميمات ذيل اتخاذ شد: علي رضايي به شماره"
           " ملي 1111111112 مريم احمدي به شماره ملي 2222222223 به عنوان اعضاي هيئت مديره براي مدت دو سال"
           " انتخاب شدند. حسن كريمي به شماره ملي 3333333334 به عنوان بازرس اصلي و سارا نوري به شماره ملي"
           " 4444444445 به سمت بازرس علي البدل براي يكسال مالي انتخاب شدند. روزنامه نمونه انتخاب گرديد.")
FOUNDING = ("-3 مركز اصلي شركت :      3-1 - استان تهران - شهر تهران خيابان نمونه پلاك 2 - كد پستي 1981900000"
            " -4 سرمايه شركت : مبلغ 000/000/1 ريال مي باشد. -5 اولين مديران شركت :     5-1 - آقاي سعيد"
            " نمونه به شماره ملي 0040000001 به سمت رئيس هيئت مديره.     5-2 - خانم نگار نمونه به شماره ملي"
            " 0440000002 به سمت عضو هيئت مديره.     5-3 - خانم نگار نمونه به شماره ملي 0440000002 به سمت"
            " مديرعامل به مدت نامحدود انتخاب گرديدند.")


def people(text):
    return [(m.name, m.entity_id, m.role) for m in extract_mentions(text) if m.kind == "person"]


def test_a_group_shares_the_role_written_after_it():
    company, *_ = extract_mentions(CHANGES)
    assert (company.kind, company.entity_id, company.name) == ("company", "10200000001", "نمونه سازان شرق")
    assert people(CHANGES) == [("علی رضایی", "1111111112", "board"), ("مریم احمدی", "2222222223", "board"),
                               ("حسن کریمی", "3333333334", "inspector"),
                               ("سارا نوری", "4444444445", "alternate_inspector")]


def test_founding_notice_address_and_list_items():
    address, *_ = extract_mentions(FOUNDING)
    assert (address.kind, address.entity_id) == ("address", "1981900000")  # not a person: it's a postal code
    assert address.name == "استان تهران - شهر تهران خیابان نمونه پلاک 2"
    assert people(FOUNDING) == [("سعید نمونه", "0040000001", "chair"), ("نگار نمونه", "0440000002", "board"),
                                ("نگار نمونه", "0440000002", "ceo")]


def test_offsets_point_into_the_original_text():
    mention = extract_mentions(FOUNDING)[1]
    assert FOUNDING[mention.start:mention.end] == "آقاي سعيد نمونه به شماره ملي 0040000001"


def test_representatives_in_both_orders():
    company_first = ("اعضاي هيات مديره عبارتند از: شركت ريل نمونه(سهامي عام) به شناسه ملي 1010400000 به"
                     " نمايندگي آقاي اسماعيل نمونه به شماره ملي 1372700000 براي مدت دوسال انتخاب گرديدند.")
    company, person = extract_mentions(company_first)
    assert (company.kind, company.name) == ("company", "ریل نمونه")  # 10 digits, but a شناسه
    assert (person.name, person.represents) == ("اسماعیل نمونه", "1010400000")

    person_first = ("آقای حجت نمونه با کد ملی 4198500000 به نمایندگی از شرکت خدمات نمونه کاسپین  با شناسه"
                    " ملی 10102500000 به سمت مدیر عامل و عضو هیئت مدیره ، آقای علی نمونه")
    person, company, last = extract_mentions(person_first)
    assert (person.name, person.role, person.represents) == ("حجت نمونه", "ceo,board", "10102500000")
    assert (company.name, company.role) == ("خدمات نمونه کاسپین", "ceo,board")
    assert (last.name, last.entity_id) == ("علی نمونه", None)  # named without a number


def test_father_name_and_bare_code():
    text = ("بازرس وبازرسين : -1 محمود علي زاده فرزند محمد كدملي 0640100000 بازرس اصلي -2 اقاي عارف نمونه"
            " فرزند درمحمد كدملي 3591400000 بعنوان بازرس علي البدل انتخاب شدند.")
    assert people(text) == [("محمود علی زاده", "0640100000", "inspector"),
                            ("عارف نمونه", "3591400000", "alternate_inspector")]


def test_blank_numbers_and_spelling():
    assert extract_mentions("آقاي نمونه به شماره ملي 0000000000") == []
    assert normalize("شركت ۱۲۳") == "شرکت 123"


def test_roles_given_by_name_after_the_list():
    # The list numbers them, then the board meeting names their roles without the numbers.
    text = ("اعضاء هیئت مدیره به مدت دو سال به قرار ذیل انتخاب گردیدند. احمد نمونه به کدملی 0011111112 و کدپستی"
            " 1981911112 و حسین نمونه به کدملی 0022222223 و بابک نمونه به کدملی 0033333334 ـ به موجب صورتجلسه هیئت"
            " مدیره مورخ 11/12/90 احمد نمونه به سمت رئیس هیئت مدیره و بابک نمونه به سمت نائب رئیس هیئت مدیره و حسین"
            " نمونه به سمت مدیرعامل تعیین گردیدند و کلیه اوراق با امضاء مدیرعامل و یکی از اعضاء هیئت مدیره معتبر است.")
    assert people(text) == [("احمد نمونه", "0011111112", "chair"), ("حسین نمونه", "0022222223", "ceo,board"),
                            ("بابک نمونه", "0033333334", "vice_chair")]


def test_a_role_stays_with_its_own_person():
    text = ("آقای علی نمونه به کد ملی 0044444445 و کدپستی 5179773777 به نمایندگی از طرف شرکت کرپ نمونه بسمت رئیس"
            " هیئت مدیره آقای رضا نمونه به کد ملی 0055555556 و کدپستی 5148668583 بسمت عضو هیئت مدیره و آقای مجید"
            " نمونه بسمت مدیرعامل انتخاب و امضاء کلیه اوراق با امضای مدیرعامل و رئیس هیئت مدیره معتبر است. آقای"
            " حسن نمونه به کد ملی 0066666667 و کدپستی 5148668584 و خانم سارا نمونه به کد ملی 0077777778 بترتیب"
            " بسمت بازرسان اصلی و علی البدل انتخاب شدند.")
    assert people(text) == [("علی نمونه", "0044444445", "chair"), ("رضا نمونه", "0055555556", "board"),
                            ("مجید نمونه", None, "ceo"),  # named without a number
                            ("حسن نمونه", "0066666667", "inspector"), ("سارا نمونه", "0077777778", "alternate_inspector")]


def test_a_board_named_without_numbers():
    # Notices before about 1390 give no national codes; an audit firm without "شرکت" is no person.
    text = ("5- مدیران و صاحبان امضاء: آقای اصغر نمونه به سمت مدیر عامل و رئیس هیئت مدیره ـ آقای قربان نمونه"
            " به سمت نائب رئیس هیئت مدیره و آقایان محمود رضا نمونه و غلامرضا نمونه به سمت اعضای هیئت مدیره و آقای"
            " کاظم نمونه با حفظ سمت به عنوان عضو علی البدل هیئت مدیره انتخاب شدند. 6- موسسه حسابرسی و خدمات مدیریت"
            " نمونه به عنوان بازرس قانونی و آقایان سید عباس نمونه و اکبر نمونه به ترتیب به سمت بازرسان اصلی و علی"
            " البدل شرکت برای مدت یک سال انتخاب شدند.")
    assert people(text) == [("اصغر نمونه", None, "chair,ceo"), ("قربان نمونه", None, "vice_chair"),
                            ("محمود رضا نمونه", None, "board"), ("غلامرضا نمونه", None, "board"),
                            ("کاظم نمونه", None, "alternate_board"), ("سید عباس نمونه", None, "inspector"),
                            ("اکبر نمونه", None, "alternate_inspector")]
    rows = ad_mentions(text, "10200443149", "نمونه")
    assert [row[1] for row in rows if row[0] == "person"][0] == "~10200443149~اصغرنمونه"
    assert ad_mentions(text, None, None) == []  # no company to tell them apart by


def test_identity_details_between_name_and_number():
    text = ("7 ـ آقای محمدهادی نمونه فرزند ناصر به ش.ش320 شیراز به ش. ملی2294996445 دارنده1 سهم<br> 8 ـ آقای پیمان"
            " نمونه فرزند کریم شماره شناسنامه 270 صادره از سرپلذهاب متولد 1354 کد ملی 3369550814 بسمت نایب رئیس هیئت مدیره")
    assert people(text) == [("محمدهادی نمونه", "2294996445", "shareholder"), ("پیمان نمونه", "3369550814", "vice_chair")]


def test_abbreviated_id_and_roles_written_before_the_names():
    company, person = extract_mentions("شرکت مونتاژ نمونه گیلان ش.م 10720226493 به نمایندگی محمود نمونه به کدملی"
                                       " 2249558019 به عنوان عضو هیئت مدیره")
    assert (company.name, company.entity_id) == ("مونتاژ نمونه گیلان", "10720226493")
    assert (person.name, person.role, person.represents) == ("محمود نمونه", "board", "10720226493")
    assert people("ادعای خود به مدیر تصفیه آقای بابک نمونه به شماره ملی 1689540011 به شماره همراه 09141220695"
                  " مستقر در آدرس تبریز") == [("بابک نمونه", "1689540011", "liquidator")]
    assert people("مراجعه به مدیران تصفیه شرکت ( داود نمونه کد ملی 1699516685 و احمد نمونه کد ملی 1373144704 )"
                  " به نشانی تبریز") == [("داود نمونه", "1699516685", "liquidator"),
                                         ("احمد نمونه", "1373144704", "liquidator")]


def test_who_leaves():
    assert people("1- قبول استعفای آقای علی نمونه به کد ملی 0011111112 از سمت مدیرعامل پذیرفته شد.") == [
        ("علی نمونه", "0011111112", "ceo,left")]
    assert people("عباس نمونه بجای سنجر نمونه به عنوان عضو اصلی هیئت مدیره برای بقیه مدت تصدی هیئت مدیره انتخاب"
                  " گردیدند.") == [("عباس نمونه", None, "board"), ("سنجر نمونه", None, "board,left")]
    assert people("قبول استعفای آقایان پژمان نمونه به کد ملی 0022222223 و مازیار نمونه به کد ملی 0033333334 و انتخاب"
                  " آقای رضا نمونه به کد ملی 0044444445 به سمت عضو هیئت مدیره") == [
        ("پژمان نمونه", "0022222223", "left"), ("مازیار نمونه", "0033333334", "left"),
        ("رضا نمونه", "0044444445", "board")]


def test_respectively_pairs_names_and_roles():
    assert people("آقایان حسین نمونه به کدملی 1156362625 و مسعود نمونه به کدملی 1282384181 به ترتیب به سمت رئیس و"
                  " نایب رئیس هیئت مدیره انتخاب شدند.") == [("حسین نمونه", "1156362625", "chair"),
                                                            ("مسعود نمونه", "1282384181", "vice_chair")]


def test_people_written_like_companies():
    # "شناسه ملی" is written for people too; a person's code with a digit too many has 11 digits.
    text = ("خانم شهلا نمونه به شناسه ملی 2372569821 بسمت رئیس هیئت مدیره و مدیرعامل شرکت و خانم نسیبه نمونه به"
            " شناسه ملی 2279959623 بسمت نایب رئیس هیئت مدیره و آقای علیرضا نمونه به شماره ملی 35498417361 به عنوان"
            " بازرس اصلی و موسسه حسابرسی نمونه تراز به شناسه ملی 1020164747 به عنوان بازرس علی البدل انتخاب شدند.")
    assert [(m.kind, m.name) for m in extract_mentions(text)] == [
        ("person", "شهلا نمونه"), ("person", "نسیبه نمونه"), ("person", "علیرضا نمونه"),
        ("company", "موسسه حسابرسی نمونه تراز")]
    assert [row[3] for row in ad_mentions(text, "10200443149", None)][:4] == [True, True, False, False]
    assert ad_mentions("", "۱4۰۰۶۲۶۱۸۶۲", None)[0][1] == "14006261862"  # the listing's Persian digits


def test_names_in_brackets_lists_and_before_their_role():
    assert people("آقای حسین نمونه (شماره ملی: 0850009723) بسمت بازرس اصلی و آقای احسان نمونه (آوات) با کد ملی"
                  " 0450485773 بسمت بازرس علی البدل") == [("حسین نمونه", "0850009723", "inspector"),
                                                         ("احسان نمونه", "0450485773", "alternate_inspector")]
    # The one before's "صادره از ..." and a full stop after the father's name.
    text = ("1 ـ آقای ابوالفضل نمونه فرزند نور اله. کد ملی 1500379409 و کدپستی 5451936138 بعنوان رئیس هیئت مدیره،"
            " غلامرضا نمونه فرزند حسن شماره شناسنامه 17810 کد ملی 2430164175 صادره از مرودشت، محمد هادی نمونه"
            " فرزند قربانعلی کد ملی 2432020375 صادره از مرودشت بسمت اعضای هیئت مدیره")
    assert people(text) == [("ابوالفضل نمونه", "1500379409", "chair"), ("غلامرضا نمونه", "2430164175", "board"),
                            ("محمد هادی نمونه", "2432020375", "board")]
    assert [m.name for m in extract_mentions(text) if m.kind == "address"] == [None]  # not the sentence before
    # The role between the name and the number: one person each, not also one without a number.
    assert people("آقای مالک نمونه به سمت رئیس هیئت مدیره به شماره ملی4011645478 آقای عقیل نمونه به سمت نایب رئیس"
                  " هیئت مدیره به شماره ملی 0061555746 و بهرام نمونه خارج از اعضاء به کدملی4070541055 به سمت"
                  " مدیرعامل") == [("مالک نمونه", "4011645478", "chair"), ("عقیل نمونه", "0061555746", "vice_chair"),
                                   ("بهرام نمونه", "4070541055", "ceo")]
    assert people("1 -غلامرضا نمونه –رئیس هیات مدیره -کد ملی 0063637561\r 2 -حسن نمونه –نایب رئیس-کد ملی"
                  " 0042848482") == [("غلامرضا نمونه", "0063637561", "chair"), ("حسن نمونه", "0042848482", "vice_chair")]


def test_a_list_under_a_heading_then_roles_by_name():
    # The heading gives the list its role; a role by name after it ("که A به سمت ...") is that one's only.
    assert people("اعضاء هیئت مدیره به مدت 2 سال به قرار ذیل انتخاب گردیدند: علی نمونه به کدملی 0044673337 و محمدعلی"
                  " نمونه به کدملی 0071555341 و مجتبی نمونه به کدملی 0060118768 که علی نمونه به سمت مدیرعامل و رئیس"
                  " هیئت مدیره، محمدعلی نمونه به سمت نایب رئیس هیئت مدیره تعیین گردیدند.") == [
        ("علی نمونه", "0044673337", "chair,ceo"), ("محمدعلی نمونه", "0071555341", "vice_chair"),
        ("مجتبی نمونه", "0060118768", "board")]
    assert people("اعضاء هیئت مدیره به مدت 2 سال بقرار ذیل انتخاب گردیدند. ابراهیم نمونه به ش ملی 0043526810 و محمدرضا"
                  " نمونه به ش ملی 0039393364 ابراهیم نمونه به سمت رئیس هیئت مدیره و محمدرضا نمونه به سمت نایب رئیس"
                  " هیئت مدیره و جعفر نمونه خارج از اعضاء به سمت مدیرعامل تعیین گردیدند.") == [
        ("ابراهیم نمونه", "0043526810", "chair"), ("محمدرضا نمونه", "0039393364", "vice_chair"),
        ("جعفر نمونه", None, "ceo")]


def test_partners_stakes():
    # What each one's share becomes, not what they paid in; a whole share given over is the new holder's.
    text = ("آقای علی نمونه با پرداخت مبلغ 4.000.000 ریال و خانم مریم نمونه با پرداخت مبلغ 3.000.000 ریال به صندوق"
            " شرکت هرکدام سهم الشرکه خود را به مبلغ 5.000.000 ریال افزایش دادند. آقای حسن نمونه به کدملی 0044444445"
            " دارنده 990.000 ریال سهم الشرکه با صلح کلیه سهم الشرکه خود به آقای رضا نمونه به کدملی 0055555556 از"
            " شرکت خارج گردید.")
    assert [(m.name, m.stake) for m in extract_mentions(text) if m.kind == "person"] == [
        ("علی نمونه", 5000000), ("مریم نمونه", 5000000), ("حسن نمونه", 990000), ("رضا نمونه", 990000)]


def test_an_amount_too_large_to_store_drops_its_stray_scale():
    from parsing.entities import amount
    assert amount("2800495500000میلیارد") == 2800495500000  # the rials, then "billion" by mistake
    assert amount("80.000.000.000 میلیارد") == 80000000000
    assert amount("2.800 میلیارد") == 2800000000000
    assert amount("1" * 25) is None
    assert amount("122\x1f میلیون") == 122000000  # a control character as the space (stalled the GUI 2026-11-01)


def test_check_digits():
    assert (valid_code("0061555746"), valid_code("4322807824")) == (True, False)  # a person's national code
    assert (valid_code("10200443149"), valid_code("10200443148")) == (True, False)  # a company's national id
    assert valid_code("123") is None
