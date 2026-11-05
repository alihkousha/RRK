from models.issuer import Issuer, office, read_issuer


def test_the_office_and_city_however_the_signature_line_is_written():
    assert read_issuer("مسئول ثبت شركتهاي ثبت تبريز") == Issuer("registry", "تبریز")
    assert read_issuer("رئيس ثبت اسناد و املاك ناحيه دو مشهد") == Issuer("deeds", "مشهد")
    assert read_issuer("اداره ثبت شرکتها و موسسات\r\nغیرتجاری واحد ثبتی زنجان") == Issuer("registry", "زنجان")
    assert read_issuer("سرپرست اداره ثبت اسناد و املاك اسد‌آباد") == Issuer("deeds", "اسد آباد")
    assert read_issuer("رئيس ثبت اسنادکرج") == Issuer("deeds", "کرج")
    assert read_issuer("اداره ثبت شرکتها و مالکیت های صنعتی و معنوی منطقه آزاد کیش") == Issuer("free_zone", "کیش")
    assert read_issuer("مدیر تصفیه شرکت نمونه (در حال تصفیه) - علی نمونه") == Issuer("liquidator", None)
    assert (office("اداره ثبت علایم تجارتی"), office("اداره ثبت شركتها و موسسات غيرتجاري"), office(" ")) == (
        "~ip", "~registry", None)
