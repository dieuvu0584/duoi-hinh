import pytest

from app.services.vi_normalizer import NORMALIZER_VERSION, normalize, number_to_words


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "không"), (1, "một"), (4, "bốn"), (5, "năm"), (9, "chín"),
        (10, "mười"), (11, "mười một"), (14, "mười bốn"), (15, "mười lăm"),
        (20, "hai mươi"), (21, "hai mươi mốt"), (24, "hai mươi bốn"), (25, "hai mươi lăm"),
        (35, "ba mươi lăm"), (99, "chín mươi chín"),
        (100, "một trăm"), (101, "một trăm linh một"), (105, "một trăm linh năm"),
        (110, "một trăm mười"), (115, "một trăm mười lăm"), (120, "một trăm hai mươi"),
        (121, "một trăm hai mươi mốt"), (999, "chín trăm chín mươi chín"),
        (1000, "một nghìn"), (1001, "một nghìn không trăm linh một"),
        (1010, "một nghìn không trăm mười"), (2026, "hai nghìn không trăm hai mươi sáu"),
        (24500, "hai mươi bốn nghìn năm trăm"),
        (100000, "một trăm nghìn"), (1000000, "một triệu"),
        (1500000, "một triệu năm trăm nghìn"), (2000000000, "hai tỷ"),
        (1000000001, "một tỷ không trăm linh một"),
    ],
)
def test_number_reading(n, words):
    assert number_to_words(n) == words


def test_out_of_range_numbers_are_rejected():
    for bad in (-1, 10**12):
        with pytest.raises(ValueError):
            number_to_words(bad)


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Hôm nay là ngày 25/10/2026.",
         "Hôm nay là ngày ngày hai mươi lăm tháng mười năm hai nghìn không trăm hai mươi sáu."),
        ("Sinh ngày 4/4/2001", "Sinh ngày ngày bốn tháng tư năm hai nghìn không trăm linh một"),
        ("ngày 3/12", "ngày ba tháng mười hai"),
        ("Còn 3/4 chiếc bánh", "Còn ba phần bốn chiếc bánh"),
        ("thứ Hai, 25/10, trời mưa", "thứ Hai, ngày hai mươi lăm tháng mười, trời mưa"),
        ("hẹn 4/4 nhé", "hẹn ngày bốn tháng tư nhé"),
        ("được 1/2 phần", "được một phần hai phần"),
        ("tỉ lệ 7/20", "tỉ lệ bảy phần hai mươi"),
    ],
)
def test_dates_and_fractions(text, spoken):
    assert normalize(text) == spoken


def test_an_invalid_date_is_never_turned_into_a_date():
    out = normalize("13/13/2026")
    assert "ngày" not in out and "tháng" not in out  # numbers are read, no date is invented
    assert not any(c.isdigit() for c in out)


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Hẹn 7h30 nhé", "Hẹn bảy giờ ba mươi phút nhé"),
        ("Dậy lúc 6h", "Dậy lúc sáu giờ"),
        ("Họp lúc 14:05", "Họp lúc mười bốn giờ linh năm phút"),
        ("Đúng 12:00", "Đúng mười hai giờ"),
        ("25:99", "hai mươi lăm:chín mươi chín"),  # not a time: plain numbers
    ],
)
def test_times(text, spoken):
    assert normalize(text) == spoken


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("giá xăng 24.500 đồng một lít", "giá xăng hai mươi bốn nghìn năm trăm đồng một lít"),
        ("giá 24.500đ", "giá hai mươi bốn nghìn năm trăm đồng"),
        ("thu 1.500.000 VND", "thu một triệu năm trăm nghìn đồng"),
        ("giảm 50%", "giảm năm mươi phần trăm"),
        ("tăng 2,5%", "tăng hai phẩy năm phần trăm"),
        ("có 3,14 vòng", "có ba phẩy một bốn vòng"),
        ("trả $20", "trả hai mươi đô la"),
        ("chạy 5km", "chạy năm ki lô mét"),
        ("nặng 3 kg", "nặng ba ki lô gam"),
        ("nóng 35°C", "nóng ba mươi lăm độ xê"),
        ("rộng 20m2", "rộng hai mươi mét vuông"),
    ],
)
def test_money_percent_decimals_and_units(text, spoken):
    assert normalize(text) == spoken


def test_leading_zero_and_long_digit_strings_are_read_digit_by_digit():
    assert normalize("mã 007") == "mã không không bảy"
    assert normalize("gọi 0912345678") == "gọi không chín một hai ba bốn năm sáu bảy tám"


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Ở TP.HCM", "Ở Thành phố Hồ Chí Minh"),
        ("UBND TP Hà Nội", "Ủy ban nhân dân thành phố Hà Nội"),
        ("Tom & Jerry", "Tom và Jerry"),
        ("xem https://example.com ngay", "xem ngay"),
        ("đoán đi #doanhinh #meme", "đoán đi"),
        ("vui quá 😀 nhé", "vui quá nhé"),
        ("**đậm** và _nghiêng_", "đậm và nghiêng"),
    ],
)
def test_abbreviations_symbols_and_noise(text, spoken):
    assert normalize(text) == spoken


def test_a_custom_lexicon_replaces_the_default():
    assert normalize("chạy xe", {"chạy": "chạy~"}) == "chạy~ xe"
    assert normalize("TP HCM", {}) == "TP HCM"


def test_unicode_is_normalised_and_whitespace_collapsed():
    nfd = "Bánh   chưng"
    assert normalize(nfd) == "Bánh chưng"


def test_empty_and_odd_input_never_raise():
    for value in ("", "   ", None, "!!!", "1/0/0", "99999999999999999999"):
        normalize(value)  # type: ignore[arg-type]


def test_the_owner_test_lines_become_plain_words():
    line = "Hôm nay là ngày 25/10/2026, trời mưa 3 ngày liền, giá xăng 24.500 đồng một lít."
    out = normalize(line)
    assert not any(c.isdigit() for c in out)
    assert "hai nghìn không trăm hai mươi sáu" in out and "hai mươi bốn nghìn năm trăm" in out


def test_version_is_an_integer_the_cache_key_can_use():
    assert isinstance(NORMALIZER_VERSION, int) and NORMALIZER_VERSION >= 1
