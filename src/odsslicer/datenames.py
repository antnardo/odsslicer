"""The names a date format spells out - days of the week and months - and
the decimal separator of its seconds, as LibreOffice shows them.

A format such as `NNNN, MMMM D, YYYY` shows "Sunday, September 27, 2026". The
text odsslicer writes for a date or a time follows the cell's own format when
it can render it, and could not render these names: a long date then took the
layout of another cell of the document, `09/27/26` (issue #16). Fractions of
a second went missing the same way, `01:24` where LibreOffice shows
`01:24.75`.

The names come from LibreOffice 25.8, for the locales of `dateformats`,
generated the same way: its number formatter showing, for each day of a week
and each month, `NNN` and `NN` - the day in full and abbreviated - `MMMM`
and `MMM`, and `D MMMM` and `D MMM`, the month next to a day, which some
languages decline: Polish "września" where the month alone is "wrzesień". A
language's names are those of its first locale in `dateformats`, and the
table records a locale apart only where they differ: Austrian German's
"Jänner". The decimal separator, which does depend on the country - `.` in
de-CH, `,` in de-DE - is LibreOffice's for each locale. A language the table
lacks has no names: a date in a format naming days or months then shows in
ISO 8601, as does a document with no language.
"""

from dataclasses import dataclass

from .dateformats import _STANDARD_FORMATS

# language -> the days of the week, Sunday first, in full and abbreviated;
# the months in full and abbreviated; the same next to a day - "|"-separated
_NAMES: "dict[str, tuple[str, str, str, str, str, str]]" = {
    "fr": (
        "dimanche|lundi|mardi|mercredi|jeudi|vendredi|samedi",
        "dim.|lun.|mar.|mer.|jeu.|ven.|sam.",
        "janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre",
        "janv.|févr.|mars|avr.|mai|juin|juil.|août|sept.|oct.|nov.|déc.",
        "janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre",
        "janv.|févr.|mars|avr.|mai|juin|juil.|août|sept.|oct.|nov.|déc.",
    ),
    "en": (
        "Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday",
        "Sun|Mon|Tue|Wed|Thu|Fri|Sat",
        "January|February|March|April|May|June|July|August|September|October|November|December",
        "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
        "January|February|March|April|May|June|July|August|September|October|November|December",
        "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
    ),
    "de": (
        "Sonntag|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag",
        "So|Mo|Di|Mi|Do|Fr|Sa",
        "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember",
        "Jan|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez",
        "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember",
        "Jan|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez",
    ),
    "es": (
        "domingo|lunes|martes|miércoles|jueves|viernes|sábado",
        "dom|lun|mar|mié|jue|vie|sáb",
        ("enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"),
        "ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic",
        ("enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"),
        "ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic",
    ),
    "it": (
        "domenica|lunedì|martedì|mercoledì|giovedì|venerdì|sabato",
        "dom|lun|mar|mer|gio|ven|sab",
        ("gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"),
        "gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic",
        ("gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"),
        "gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic",
    ),
    "pt": (
        "domingo|segunda-feira|terça-feira|quarta-feira|quinta-feira|sexta-feira|sábado",
        "dom|seg|ter|qua|qui|sex|sáb",
        ("janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"),
        "jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez",
        ("janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"),
        "jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez",
    ),
    "nl": (
        "zondag|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag",
        "zo|ma|di|wo|do|vr|za",
        ("januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"),
        "jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec",
        ("januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"),
        "jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec",
    ),
    "ca": (
        "diumenge|dilluns|dimarts|dimecres|dijous|divendres|dissabte",
        "dg.|dl.|dt.|dc.|dj.|dv.|ds.",
        "gener|febrer|març|abril|maig|juny|juliol|agost|setembre|octubre|novembre|desembre",
        "gen.|febr.|març|abr.|maig|juny|jul.|ag.|set.|oct.|nov.|des.",
        "gener|febrer|març|abril|maig|juny|juliol|agost|setembre|octubre|novembre|desembre",
        "gen.|febr.|març|abr.|maig|juny|jul.|ag.|set.|oct.|nov.|des.",
    ),
    "gl": (
        "domingo|luns|martes|mércores|xoves|venres|sábado",
        "dom|lun|mar|mér|xov|ven|sáb",
        ("xaneiro|febreiro|marzo|abril|maio|xuño|xullo|agosto|setembro|outubro|novembro|decembro"),
        "xan|feb|mar|abr|mai|xuñ|xul|ago|set|out|nov|dec",
        ("xaneiro|febreiro|marzo|abril|maio|xuño|xullo|agosto|setembro|outubro|novembro|decembro"),
        "xan|feb|mar|abr|mai|xuñ|xul|ago|set|out|nov|dec",
    ),
    "eu": (
        "igandea|astelehena|asteartea|asteazkena|osteguna|ostirala|larunbata",
        "ig.|al.|ar.|az.|og.|or.|lr.",
        ("urtarrila|otsaila|martxoa|apirila|maiatza|ekaina|uztaila|abuztua|iraila|urria|azaroa|abendua"),
        "urt|ots|mar|api|mai|eka|uzt|abu|ira|urr|aza|abe",
        ("urtarrila|otsaila|martxoa|apirila|maiatza|ekaina|uztaila|abuztua|iraila|urria|azaroa|abendua"),
        "urt|ots|mar|api|mai|eka|uzt|abu|ira|urr|aza|abe",
    ),
    "sv": (
        "söndag|måndag|tisdag|onsdag|torsdag|fredag|lördag",
        "sö|må|ti|on|to|fr|lö",
        "januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december",
        "jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec",
        "januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december",
        "jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec",
    ),
    "da": (
        "søndag|mandag|tirsdag|onsdag|torsdag|fredag|lørdag",
        "søn|man|tir|ons|tor|fre|lør",
        "januar|februar|marts|april|maj|juni|juli|august|september|oktober|november|december",
        "jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec",
        "januar|februar|marts|april|maj|juni|juli|august|september|oktober|november|december",
        "jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec",
    ),
    "nb": (
        "søndag|mandag|tirsdag|onsdag|torsdag|fredag|lørdag",
        "sø.|ma.|ti.|on.|to.|fr.|lø.",
        "januar|februar|mars|april|mai|juni|juli|august|september|oktober|november|desember",
        "jan.|feb.|mars|april|mai|juni|juli|aug.|sep.|okt.|nov.|des.",
        "januar|februar|mars|april|mai|juni|juli|august|september|oktober|november|desember",
        "jan.|feb.|mars|april|mai|juni|juli|aug.|sep.|okt.|nov.|des.",
    ),
    "nn": (
        "sundag|måndag|tysdag|onsdag|torsdag|fredag|laurdag",
        "su.|må.|ty.|on.|to.|fr.|la.",
        "januar|februar|mars|april|mai|juni|juli|august|september|oktober|november|desember",
        "jan.|feb.|mars|april|mai|juni|juli|aug.|sep.|okt.|nov.|des.",
        "januar|februar|mars|april|mai|juni|juli|august|september|oktober|november|desember",
        "jan.|feb.|mars|april|mai|juni|juli|aug.|sep.|okt.|nov.|des.",
    ),
    "fi": (
        "sunnuntai|maanantai|tiistai|keskiviikko|torstai|perjantai|lauantai",
        "su|ma|ti|ke|to|pe|la",
        (
            "tammikuu|helmikuu|maaliskuu|huhtikuu|toukokuu|kesäkuu|heinäkuu|elokuu|syyskuu"
            "|lokakuu|marraskuu|joulukuu"
        ),
        "tammi|helmi|maalis|huhti|touko|kesä|heinä|elo|syys|loka|marras|joulu",
        (
            "tammikuuta|helmikuuta|maaliskuuta|huhtikuuta|toukokuuta|kesäkuuta|heinäkuuta"
            "|elokuuta|syyskuuta|lokakuuta|marraskuuta|joulukuuta"
        ),
        "tammi|helmi|maalis|huhti|touko|kesä|heinä|elo|syys|loka|marras|joulu",
    ),
    "is": (
        "sunnudagur|mánudagur|þriðjudagur|miðvikudagur|fimmtudagur|föstudagur|laugardagur",
        "sun|mán|þri|mið|fim|fös|lau",
        "janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember",
        "jan|feb|mar|apr|maí|jún|júl|ágú|sep|okt|nóv|des",
        "janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember",
        "jan|feb|mar|apr|maí|jún|júl|ágú|sep|okt|nóv|des",
    ),
    "pl": (
        "niedziela|poniedziałek|wtorek|środa|czwartek|piątek|sobota",
        "niedz.|pon.|wt.|śr.|czw.|pt.|sob.",
        ("styczeń|luty|marzec|kwiecień|maj|czerwiec|lipiec|sierpień|wrzesień|październik|listopad|grudzień"),
        "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII",
        (
            "stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października"
            "|listopada|grudnia"
        ),
        "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII",
    ),
    "cs": (
        "neděle|pondělí|úterý|středa|čtvrtek|pátek|sobota",
        "ne|po|út|st|čt|pá|so",
        "leden|únor|březen|duben|květen|červen|červenec|srpen|září|říjen|listopad|prosinec",
        "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII",
        "ledna|února|března|dubna|května|června|července|srpna|září|října|listopadu|prosince",
        "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII",
    ),
    "sk": (
        "Nedeľa|Pondelok|Utorok|Streda|Štvrtok|Piatok|Sobota",
        "Ne|Po|Ut|St|Št|Pi|So",
        "január|február|marec|apríl|máj|jún|júl|august|september|október|november|december",
        "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII",
        ("januára|februára|marca|apríla|mája|júna|júla|augusta|septembra|októbra|novembra|decembra"),
        "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII",
    ),
    "hu": (
        "vasárnap|hétfő|kedd|szerda|csütörtök|péntek|szombat",
        "V|H|K|Sze|Cs|P|Szo",
        ("január|február|március|április|május|június|július|augusztus|szeptember|október|november|december"),
        "I.|II.|III.|IV.|V.|VI.|VII.|VIII.|IX.|X.|XI.|XII.",
        ("január|február|március|április|május|június|július|augusztus|szeptember|október|november|december"),
        "I.|II.|III.|IV.|V.|VI.|VII.|VIII.|IX.|X.|XI.|XII.",
    ),
    "ro": (
        "duminică|luni|marți|miercuri|joi|vineri|sâmbătă",
        "dum.|lun.|mar.|mie.|joi.|vin.|sâm.",
        ("ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|septembrie|octombrie|noiembrie|decembrie"),
        "ian|febr|mar|apr|mai|iun|iul|aug|sept|oct|nov|dec",
        ("ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|septembrie|octombrie|noiembrie|decembrie"),
        "ian|febr|mar|apr|mai|iun|iul|aug|sept|oct|nov|dec",
    ),
    "bg": (
        "неделя|понеделник|вторник|сряда|четвъртък|петък|събота",
        "нд|пн|вт|ср|чт|пт|сб",
        "януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември",
        "яну|фев|мар|апр|май|юни|юли|авг|сеп|окт|ное|дек",
        "януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември",
        "яну|фев|мар|апр|май|юни|юли|авг|сеп|окт|ное|дек",
    ),
    "el": (
        "Κυριακή|Δευτέρα|Τρίτη|Τετάρτη|Πέμπτη|Παρασκευή|Σάββατο",
        "Κυρ|Δευ|Τρι|Τετ|Πεμ|Παρ|Σαβ",
        (
            "Ιανουαρίου|Φεβρουαρίου|Μαρτίου|Απριλίου|Μαΐου|Ιουνίου|Ιουλίου|Αυγούστου"
            "|Σεπτεμβρίου|Οκτωβρίου|Νοεμβρίου|Δεκεμβρίου"
        ),
        "Ιαν|Φεβ|Μαρ|Απρ|Μαϊ|Ιουν|Ιουλ|Αυγ|Σεπ|Οκτ|Νοε|Δεκ",
        (
            "Ιανουαρίου|Φεβρουαρίου|Μαρτίου|Απριλίου|Μαΐου|Ιουνίου|Ιουλίου|Αυγούστου"
            "|Σεπτεμβρίου|Οκτωβρίου|Νοεμβρίου|Δεκεμβρίου"
        ),
        "Ιαν|Φεβ|Μαρ|Απρ|Μαϊ|Ιουν|Ιουλ|Αυγ|Σεπ|Οκτ|Νοε|Δεκ",
    ),
    "hr": (
        "nedjelja|ponedjeljak|utorak|srijeda|četvrtak|petak|subota",
        "ned|pon|uto|sri|čet|pet|sub",
        ("siječanj|veljača|ožujak|travanj|svibanj|lipanj|srpanj|kolovoz|rujan|listopad|studeni|prosinac"),
        "sij|velj|ožu|tra|svi|lip|srp|kol|ruj|lis|stu|pro",
        ("siječnja|veljače|ožujka|travnja|svibnja|lipnja|srpnja|kolovoza|rujna|listopada|studenog|prosinca"),
        "sij|velj|ožu|tra|svi|lip|srp|kol|ruj|lis|stu|pro",
    ),
    "sl": (
        "nedelja|ponedeljek|torek|sreda|četrtek|petek|sobota",
        "ned|pon|tor|sre|čet|pet|sob",
        "januar|februar|marec|april|maj|junij|julij|avgust|september|oktober|november|december",
        "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec",
        "januar|februar|marec|april|maj|junij|julij|avgust|september|oktober|november|december",
        "jan|feb|mar|apr|maj|jun|jul|avg|sep|okt|nov|dec",
    ),
    "sr": (
        "недеља|понедељак|уторак|среда|четвртак|петак|субота",
        "нед|пон|уто|сре|чет|пет|суб",
        "јануар|фебруар|март|април|мај|јун|јул|август|септембар|октобар|новембар|децембар",
        "јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец",
        "јануар|фебруар|март|април|мај|јун|јул|август|септембар|октобар|новембар|децембар",
        "јан|феб|мар|апр|мај|јун|јул|авг|сеп|окт|нов|дец",
    ),
    "et": (
        "pühapäev|esmaspäev|teisipäev|kolmapäev|neljapäev|reede|laupäev",
        "P|E|T|K|N|R|L",
        ("jaanuar|veebruar|märts|aprill|mai|juuni|juuli|august|september|oktoober|november|detsember"),
        "jaan|veebr|märts|apr|mai|juuni|juuli|aug|sept|okt|nov|dets",
        ("jaanuar|veebruar|märts|aprill|mai|juuni|juuli|august|september|oktoober|november|detsember"),
        "jaan|veebr|märts|apr|mai|juuni|juuli|aug|sept|okt|nov|dets",
    ),
    "lv": (
        "svētdiena|pirmdiena|otrdiena|trešdiena|ceturtdiena|piektdiena|sestdiena",
        "Sv|P|O|T|C|Pk|S",
        (
            "janvāris|februāris|marts|aprīlis|maijs|jūnijs|jūlijs|augusts|septembris|oktobris"
            "|novembris|decembris"
        ),
        "jan|feb|mar|apr|mai|jūn|jūl|aug|sep|okt|nov|dec",
        (
            "janvāris|februāris|marts|aprīlis|maijs|jūnijs|jūlijs|augusts|septembris|oktobris"
            "|novembris|decembris"
        ),
        "jan|feb|mar|apr|mai|jūn|jūl|aug|sep|okt|nov|dec",
    ),
    "lt": (
        ("sekmadienis|pirmadienis|antradienis|trečiadienis|ketvirtadienis|penktadienis|šeštadienis"),
        "Sk|Pr|An|Tr|Kt|Pn|Št",
        ("sausis|vasaris|kovas|balandis|gegužė|birželis|liepa|rugpjūtis|rugsėjis|spalis|lapkritis|gruodis"),
        "Sau|Vas|Kov|Bal|Geg|Bir|Lie|Rgp|Rgs|Spl|Lap|Grd",
        ("sausio|vasario|kovo|balandžio|gegužės|birželio|liepos|rugpjūčio|rugsėjo|spalio|lapkričio|gruodžio"),
        "Sau|Vas|Kov|Bal|Geg|Bir|Lie|Rgp|Rgs|Spl|Lap|Grd",
    ),
    "ru": (
        "воскресенье|понедельник|вторник|среда|четверг|пятница|суббота",
        "Вс|Пн|Вт|Ср|Чт|Пт|Сб",
        "Январь|Февраль|Март|Апрель|Май|Июнь|Июль|Август|Сентябрь|Октябрь|Ноябрь|Декабрь",
        "янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек",
        "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря",
        "янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек",
    ),
    "uk": (
        "неділя|понеділок|вівторок|середа|четвер|п'ятниця|субота",
        "Нд|Пн|Вт|Ср|Чт|Пт|Сб",
        ("Січень|Лютий|Березень|Квітень|Травень|Червень|Липень|Серпень|Вересень|Жовтень|Листопад|Грудень"),
        "січ|лют|бер|квт|трв|чер|лип|сер|вер|жов|лис|гру",
        ("січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня"),
        "січ|лют|бер|квт|трв|чер|лип|сер|вер|жов|лис|гру",
    ),
    "be": (
        "нядзеля|панядзелак|аўторак|серада|чацвер|пятніца|субота",
        "Нд|Пн|Ат|Ср|Чц|Пт|Сб",
        (
            "студзень|люты|сакавік|красавік|травень|чэрвень|ліпень|жнівень|верасень|кастрычнік"
            "|лістапад|снежань"
        ),
        "сту|лют|сак|кра|тра|чэр|ліп|жні|вер|кас|ліс|сне",
        (
            "студзеня|лютага|сакавіка|красавіка|траўня|чэрвеня|ліпеня|жніўня|верасня"
            "|кастрычніка|лістапада|снежня"
        ),
        "сту|лют|сак|кра|тра|чэр|ліп|жні|вер|кас|ліс|сне",
    ),
    "tr": (
        "Pazar|Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi",
        "Paz|Pzt|Sal|Çar|Per|Cum|Cmt",
        "Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık",
        "Oca|Şub|Mar|Nis|May|Haz|Tem|Ağu|Eyl|Eki|Kas|Ara",
        "Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık",
        "Oca|Şub|Mar|Nis|May|Haz|Tem|Ağu|Eyl|Eki|Kas|Ara",
    ),
    "he": (
        "ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת",
        "א|ב|ג|ד|ה|ו|ש",
        "ינואר|פברואר|מרץ|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר",
        "ינו|פבר|מרץ|אפר|מאי|יוני|יולי|אוג|ספט|אוק|נוב|דצמ",
        "ינואר|פברואר|מרץ|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר",
        "ינו|פבר|מרץ|אפר|מאי|יוני|יולי|אוג|ספט|אוק|נוב|דצמ",
    ),
    "ja": (
        "日曜日|月曜日|火曜日|水曜日|木曜日|金曜日|土曜日",
        "日|月|火|水|木|金|土",
        "一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月",
        "1月|2月|3月|4月|5月|6月|7月|8月|9月|10月|11月|12月",
        "一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月",
        "1月|2月|3月|4月|5月|6月|7月|8月|9月|10月|11月|12月",
    ),
    "ko": (
        "일요일|월요일|화요일|수요일|목요일|금요일|토요일",
        "일|월|화|수|목|금|토",
        "January|February|March|April|May|June|July|August|September|October|November|December",
        "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
        "January|February|March|April|May|June|July|August|September|October|November|December",
        "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
    ),
    "zh": (
        "星期日|星期一|星期二|星期三|星期四|星期五|星期六",
        "日|一|二|三|四|五|六",
        "一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月",
        "1月|2月|3月|4月|5月|6月|7月|8月|9月|10月|11月|12月",
        "一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月",
        "1月|2月|3月|4月|5月|6月|7月|8月|9月|10月|11月|12月",
    ),
    "id": (
        "Minggu|Senin|Selasa|Rabu|Kamis|Jumat|Sabtu",
        "Min|Sen|Sel|Rab|Kam|Jum|Sab",
        ("Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember"),
        "Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des",
        ("Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember"),
        "Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des",
    ),
    "ms": (
        "Ahad|Isnin|Selasa|Rabu|Khamis|Jumaat|Sabtu",
        "Ahd|Isn|Sel|Rab|Kha|Jum|Sab",
        "Januari|Februari|Mac|April|Mei|Jun|Julai|Ogos|September|Oktober|November|Disember",
        "Jan|Feb|Mac|Apr|Mei|Jun|Jul|Ogos|Sep|Okt|Nov|Dis",
        "Januari|Februari|Mac|April|Mei|Jun|Julai|Ogos|September|Oktober|November|Disember",
        "Jan|Feb|Mac|Apr|Mei|Jun|Jul|Ogos|Sep|Okt|Nov|Dis",
    ),
    "th": (
        "อาทิตย์|จันทร์|อังคาร|พุธ|พฤหัสบดี|ศุกร์|เสาร์",
        "อา.|จ.|อ.|พ.|พฤ.|ศ.|ส.",
        ("มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม"),
        "ม.ค.|ก.พ.|มี.ค.|เม.ย.|พ.ค.|มิ.ย.|ก.ค.|ส.ค.|ก.ย.|ต.ค.|พ.ย.|ธ.ค.",
        ("มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม"),
        "ม.ค.|ก.พ.|มี.ค.|เม.ย.|พ.ค.|มิ.ย.|ก.ค.|ส.ค.|ก.ย.|ต.ค.|พ.ย.|ธ.ค.",
    ),
    "hi": (
        "रविवार|सॊमवार्|मंगलवार|बुधवार|गुरुवार|शुक्रवार|शनिवार",
        "रवि.|सॊम.|मंगल.|बुध.|गुरु.|शुक्र.|शनि.",
        "जनवरी|फरवरी|मार्च|अप्रॆल|मई|जून|जुलाइ|अगस्त|सितंबर|अक्तॊबर|नवंबर|दिसंबर",
        "जनवरी.|फरवरी.|मार्च.|अप्रॆल.|मई.|जून.|जुलाइ.|अगस्त.|सितंबर.|अक्तॊबर.|नवंबर.|दिसेंबर.",
        "जनवरी|फरवरी|मार्च|अप्रॆल|मई|जून|जुलाइ|अगस्त|सितंबर|अक्तॊबर|नवंबर|दिसंबर",
        "जनवरी.|फरवरी.|मार्च.|अप्रॆल.|मई.|जून.|जुलाइ.|अगस्त.|सितंबर.|अक्तॊबर.|नवंबर.|दिसेंबर.",
    ),
}
_LOCALE_NAMES: "dict[str, tuple[str, str, str, str, str, str]]" = {
    "de-AT": (
        "Sonntag|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag",
        "So|Mo|Di|Mi|Do|Fr|Sa",
        "Jänner|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember",
        "Jän|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez",
        "Jänner|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember",
        "Jän|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez",
    ),
}
_POINT_LOCALES = frozenset(
    {
        "de-CH",
        "en-AU",
        "en-CA",
        "en-GB",
        "en-IE",
        "en-IN",
        "en-NZ",
        "en-US",
        "en-ZA",
        "es-MX",
        "es-PE",
        "fr-CH",
        "he-IL",
        "hi-IN",
        "it-CH",
        "ja-JP",
        "ko-KR",
        "ms-MY",
        "th-TH",
        "zh-CN",
        "zh-HK",
        "zh-TW",
    }
)


@dataclass(frozen=True, slots=True)
class _DateNames:
    """A locale's names for the days of the week, Sunday first, and for the
    months, in full and abbreviated, alone and next to a day; and the
    decimal separator of its fractions of a second."""

    days: tuple[str, ...]
    short_days: tuple[str, ...]
    months: tuple[str, ...]
    short_months: tuple[str, ...]
    months_with_day: tuple[str, ...]
    short_months_with_day: tuple[str, ...]
    decimal: str


def _date_names(language: "str | None", country: "str | None") -> "_DateNames | None":
    """The names of `language`-`country`, its language's if the table does
    not know the country; `None` for a language it does not know."""
    if not language:
        return None
    locale = f"{language}-{country}"
    entry = _LOCALE_NAMES.get(locale) or _NAMES.get(language)
    if entry is None:
        return None
    if locale not in _STANDARD_FORMATS:  # the language's first country's
        locale = next(code for code in _STANDARD_FORMATS if code.startswith(f"{language}-"))
    days, short_days, months, short_months, with_day, short_with_day = (
        tuple(names.split("|")) for names in entry
    )
    return _DateNames(
        days,
        short_days,
        months,
        short_months,
        with_day,
        short_with_day,
        "." if locale in _POINT_LOCALES else ",",
    )
