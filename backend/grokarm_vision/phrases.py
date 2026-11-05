"""Short spoken sentences for locate results, in the seven supported languages."""

from __future__ import annotations

COLOUR_NAMES: dict[str, dict[str, str]] = {
    "en": {"red": "red", "orange": "orange", "yellow": "yellow", "green": "green", "blue": "blue",
           "purple": "purple", "pink": "pink", "white": "white", "grey": "grey", "black": "black"},
    "it": {"red": "rosso", "orange": "arancione", "yellow": "giallo", "green": "verde", "blue": "blu",
           "purple": "viola", "pink": "rosa", "white": "bianco", "grey": "grigio", "black": "nero"},
    "zh": {"red": "红色", "orange": "橙色", "yellow": "黄色", "green": "绿色", "blue": "蓝色",
           "purple": "紫色", "pink": "粉色", "white": "白色", "grey": "灰色", "black": "黑色"},
    "es": {"red": "rojo", "orange": "naranja", "yellow": "amarillo", "green": "verde", "blue": "azul",
           "purple": "morado", "pink": "rosa", "white": "blanco", "grey": "gris", "black": "negro"},
    "fr": {"red": "rouge", "orange": "orange", "yellow": "jaune", "green": "vert", "blue": "bleu",
           "purple": "violet", "pink": "rose", "white": "blanc", "grey": "gris", "black": "noir"},
    "de": {"red": "rot", "orange": "orange", "yellow": "gelb", "green": "grün", "blue": "blau",
           "purple": "lila", "pink": "rosa", "white": "weiß", "grey": "grau", "black": "schwarz"},
    "ja": {"red": "赤", "orange": "オレンジ", "yellow": "黄色", "green": "緑", "blue": "青",
           "purple": "紫", "pink": "ピンク", "white": "白", "grey": "灰色", "black": "黒"},
}

SIDES: dict[str, dict[str, str]] = {
    "en": {"left": "on the left", "right": "on the right", "centre": "in the centre"},
    "it": {"left": "a sinistra", "right": "a destra", "centre": "al centro"},
    "zh": {"left": "在左边", "right": "在右边", "centre": "在中间"},
    "es": {"left": "a la izquierda", "right": "a la derecha", "centre": "en el centro"},
    "fr": {"left": "à gauche", "right": "à droite", "centre": "au centre"},
    "de": {"left": "links", "right": "rechts", "centre": "in der Mitte"},
    "ja": {"left": "左側", "right": "右側", "centre": "中央"},
}

OBJECT_OF_COLOUR = {
    "en": "the {c} object", "it": "l'oggetto {c}", "zh": "{c}的物体", "es": "el objeto {c}",
    "fr": "l'objet {c}", "de": "das {c} Objekt", "ja": "{c}の物",
}

_GERMAN_ADJ = {"rot": "rote", "gelb": "gelbe", "grün": "grüne", "blau": "blaue", "weiß": "weiße",
               "grau": "graue", "schwarz": "schwarze"}

MESSAGES: dict[str, dict[str, str]] = {
    "found": {
        "en": "I see {label} {side}, {dist} mm from the robot base.",
        "it": "Vedo {label} {side}, a {dist} mm dalla base del robot.",
        "zh": "我看到{label}{side}，距离机器人底座 {dist} 毫米。",
        "es": "Veo {label} {side}, a {dist} mm de la base del robot.",
        "fr": "Je vois {label} {side}, à {dist} mm de la base du robot.",
        "de": "Ich sehe {label} {side}, {dist} mm von der Roboterbasis entfernt.",
        "ja": "{label}が{side}に見えます。ロボットの土台から {dist} mm です。",
    },
    "ambiguous": {
        "en": "I see {n} objects that could match: {options}. Which one do you mean?",
        "it": "Vedo {n} oggetti possibili: {options}. Quale intendi?",
        "zh": "我看到 {n} 个可能的物体：{options}。你指的是哪一个？",
        "es": "Veo {n} objetos posibles: {options}. ¿Cuál quieres decir?",
        "fr": "Je vois {n} objets possibles : {options}. Lequel veux-tu dire ?",
        "de": "Ich sehe {n} mögliche Objekte: {options}. Welches meinst du?",
        "ja": "候補が {n} 個あります：{options}。どれですか？",
    },
    "none": {
        "en": "I don't see that object on the table.",
        "it": "Non vedo quell'oggetto sul tavolo.",
        "zh": "我在桌上没有看到这个物体。",
        "es": "No veo ese objeto en la mesa.",
        "fr": "Je ne vois pas cet objet sur la table.",
        "de": "Ich sehe dieses Objekt nicht auf dem Tisch.",
        "ja": "その物はテーブルの上に見当たりません。",
    },
    "empty_table": {
        "en": "I don't see any object on the table.",
        "it": "Non vedo nessun oggetto sul tavolo.",
        "zh": "桌上没有看到任何物体。",
        "es": "No veo ningún objeto en la mesa.",
        "fr": "Je ne vois aucun objet sur la table.",
        "de": "Ich sehe kein Objekt auf dem Tisch.",
        "ja": "テーブルの上に物が見当たりません。",
    },
    "outside_workspace": {
        "en": "{label} is outside the calibrated area. I will not move there.",
        "it": "{label} è fuori dall'area calibrata. Non mi muovo lì.",
        "zh": "{label}在标定区域之外，我不会移动过去。",
        "es": "{label} está fuera del área calibrada. No me moveré allí.",
        "fr": "{label} est hors de la zone calibrée. Je n'irai pas là.",
        "de": "{label} liegt außerhalb des kalibrierten Bereichs. Dorthin fahre ich nicht.",
        "ja": "{label}は校正した範囲の外です。そこへは動きません。",
    },
    "target_moved": {
        "en": "{label} has moved by {dist} mm. I will not continue.",
        "it": "{label} si è spostato di {dist} mm. Non continuo.",
        "zh": "{label}移动了 {dist} 毫米，我不会继续。",
        "es": "{label} se ha movido {dist} mm. No continúo.",
        "fr": "{label} a bougé de {dist} mm. Je ne continue pas.",
        "de": "{label} hat sich um {dist} mm bewegt. Ich mache nicht weiter.",
        "ja": "{label}が {dist} mm 動きました。続行しません。",
    },
    "camera_moved": {
        "en": "The camera has moved since calibration. Please calibrate again.",
        "it": "La fotocamera si è spostata dopo la calibrazione. Ricalibra, per favore.",
        "zh": "相机在标定后被移动了，请重新标定。",
        "es": "La cámara se ha movido desde la calibración. Calibra de nuevo, por favor.",
        "fr": "La caméra a bougé depuis la calibration. Merci de recalibrer.",
        "de": "Die Kamera hat sich seit der Kalibrierung bewegt. Bitte neu kalibrieren.",
        "ja": "校正後にカメラが動きました。もう一度校正してください。",
    },
    "camera_check_failed": {
        "en": "I cannot confirm that the camera is still where it was calibrated.",
        "it": "Non riesco a verificare che la fotocamera sia ancora nella posizione calibrata.",
        "zh": "我无法确认相机还在标定时的位置。",
        "es": "No puedo confirmar que la cámara siga donde se calibró.",
        "fr": "Je ne peux pas confirmer que la caméra est toujours à sa place de calibration.",
        "de": "Ich kann nicht bestätigen, dass die Kamera noch an der kalibrierten Stelle ist.",
        "ja": "カメラが校正時の位置にあるか確認できません。",
    },
    "no_frame": {
        "en": "No recent picture from the camera.",
        "it": "Nessuna immagine recente dalla fotocamera.",
        "zh": "没有收到相机的最新画面。",
        "es": "No hay una imagen reciente de la cámara.",
        "fr": "Aucune image récente de la caméra.",
        "de": "Kein aktuelles Bild von der Kamera.",
        "ja": "カメラから最新の画像が届いていません。",
    },
    "not_ready": {
        "en": "The camera is not set up yet: {what}.",
        "it": "La fotocamera non è ancora pronta: {what}.",
        "zh": "相机还没有准备好：{what}。",
        "es": "La cámara aún no está lista: {what}.",
        "fr": "La caméra n'est pas encore prête : {what}.",
        "de": "Die Kamera ist noch nicht eingerichtet: {what}.",
        "ja": "カメラの準備がまだです：{what}。",
    },
}


def lang_or_default(lang: str | None) -> str:
    code = (lang or "en").lower()[:2]
    return code if code in SIDES else "en"


def colour(lang: str, name: str) -> str:
    return COLOUR_NAMES[lang].get(name, name)


def object_label(lang: str, colour_key: str) -> str:
    name = colour(lang, colour_key)
    if lang == "de":
        name = _GERMAN_ADJ.get(name, name)
    return OBJECT_OF_COLOUR[lang].format(c=name)


def side(lang: str, key: str) -> str:
    return SIDES[lang][key]


def say(key: str, lang: str, **values) -> str:
    return MESSAGES[key][lang].format(**values)
