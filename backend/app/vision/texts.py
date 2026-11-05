"""Localised operator messages for the inspection flow (spoken by Grok, shown in the UI)."""

from __future__ import annotations

T: dict[str, dict[str, str]] = {
    "verdict_PASS": {"it": "APPROVATO", "en": "PASS", "zh": "合格", "es": "APROBADO", "fr": "CONFORME", "de": "IN ORDNUNG",
                     "ja": "合格"},
    "verdict_FAIL": {"it": "SCARTATO", "en": "FAIL", "zh": "不合格", "es": "RECHAZADO", "fr": "NON CONFORME",
                     "de": "FEHLERHAFT", "ja": "不合格"},
    "bring": {
        "it": "Prelevo il pezzo dal vassoio {tray} e lo porto davanti alla telecamera. Confermi?",
        "en": "I will pick the part from tray {tray} and bring it to the camera. Proceed?",
        "zh": "我将从 {tray} 托盘取出零件并送到相机前。确认吗？",
        "es": "Tomo la pieza de la bandeja {tray} y la llevo a la cámara. ¿Confirmas?",
        "fr": "Je prends la pièce du bac {tray} et je l'amène devant la caméra. Tu confirmes ?",
        "de": "Ich nehme das Teil aus Schale {tray} und bringe es zur Kamera. Bestätigst du?",
        "ja": "トレイ{tray}から部品を取り、カメラの前に運びます。よろしいですか？",
    },
    "question": {
        "it": "Ispezione completata. Esito: {verdict}. {reason} Lo deposito nel vassoio {tray}. Confermi?",
        "en": "Inspection complete. Result: {verdict}. {reason} I will place it in tray {tray}. Proceed?",
        "zh": "检测完成。结果：{verdict}。{reason} 我将把它放进 {tray} 托盘。确认吗？",
        "es": "Inspección completada. Resultado: {verdict}. {reason} Lo dejo en la bandeja {tray}. ¿Confirmas?",
        "fr": "Inspection terminée. Résultat : {verdict}. {reason} Je le dépose dans le bac {tray}. Tu confirmes ?",
        "de": "Prüfung abgeschlossen. Ergebnis: {verdict}. {reason} Ich lege es in Schale {tray}. Bestätigst du?",
        "ja": "検査完了。結果：{verdict}。{reason} トレイ{tray}に置きます。よろしいですか？",
    },
    "unsure": {
        "it": "Non riesco a giudicare il pezzo. {reason} Decidi tu: approvato (vassoio {pass_tray}) o scartato "
              "(vassoio {fail_tray})?",
        "en": "I cannot judge this part. {reason} Your call: approve (tray {pass_tray}) or reject (tray {fail_tray})?",
        "zh": "我无法判断这个零件。{reason} 请你决定：合格（{pass_tray} 托盘）还是不合格（{fail_tray} 托盘）？",
        "es": "No puedo juzgar la pieza. {reason} Decide tú: aprobar (bandeja {pass_tray}) o rechazar "
              "(bandeja {fail_tray}).",
        "fr": "Je ne peux pas juger la pièce. {reason} À toi de décider : accepter (bac {pass_tray}) ou rejeter "
              "(bac {fail_tray}) ?",
        "de": "Ich kann das Teil nicht beurteilen. {reason} Deine Entscheidung: annehmen (Schale {pass_tray}) oder "
              "aussortieren (Schale {fail_tray})?",
        "ja": "この部品は判断できません。{reason} 合格（トレイ{pass_tray}）か不合格（トレイ{fail_tray}）か決めてください。",
    },
    "no_frame": {
        "it": "Nessuna immagine recente dalla telecamera: controlla la telecamera, poi chiedi di ripetere l'ispezione.",
        "en": "No recent camera image: check the camera, then ask me to inspect again.",
        "zh": "没有收到最新的相机画面：请检查相机，然后让我重新检测。",
        "es": "No hay imagen reciente de la cámara: revísala y pídeme repetir la inspección.",
        "fr": "Aucune image récente de la caméra : vérifie-la puis demande-moi de refaire l'inspection.",
        "de": "Kein aktuelles Kamerabild: prüfe die Kamera und bitte mich dann, erneut zu prüfen.",
        "ja": "カメラの最新画像がありません。カメラを確認してから再検査を指示してください。",
    },
    "vlm_error": {
        "it": "Il modello di visione non ha risposto ({error}).", "en": "The vision model did not answer ({error}).",
        "zh": "视觉模型没有给出结果（{error}）。", "es": "El modelo de visión no respondió ({error}).",
        "fr": "Le modèle de vision n'a pas répondu ({error}).", "de": "Das Bildmodell hat nicht geantwortet ({error}).",
        "ja": "画像モデルが応答しませんでした（{error}）。",
    },
    "next_part": {
        "it": "Pezzo depositato nel vassoio {tray}. Metti il prossimo pezzo nel vassoio {input_tray} e dimmi di "
              "continuare l'ispezione.",
        "en": "Part placed in tray {tray}. Put the next part in tray {input_tray} and tell me to continue the inspection.",
        "zh": "零件已放进 {tray} 托盘。请把下一个零件放进 {input_tray} 托盘，然后让我继续检测。",
        "es": "Pieza dejada en la bandeja {tray}. Pon la siguiente en la bandeja {input_tray} y dime que continúe.",
        "fr": "Pièce déposée dans le bac {tray}. Mets la suivante dans le bac {input_tray} et dis-moi de continuer.",
        "de": "Teil in Schale {tray} abgelegt. Lege das nächste Teil in Schale {input_tray} und sag mir, ich soll "
              "weitermachen.",
        "ja": "部品をトレイ{tray}に置きました。次の部品をトレイ{input_tray}に置いて、検査の続行を指示してください。",
    },
}


T["colour"] = {
    "red": {"it": "rosso", "en": "red", "zh": "红色", "es": "rojo", "fr": "rouge", "de": "rote", "ja": "赤い"},
    "orange": {"it": "arancione", "en": "orange", "zh": "橙色", "es": "naranja", "fr": "orange", "de": "orange",
               "ja": "オレンジの"},
    "yellow": {"it": "giallo", "en": "yellow", "zh": "黄色", "es": "amarillo", "fr": "jaune", "de": "gelbe", "ja": "黄色い"},
    "green": {"it": "verde", "en": "green", "zh": "绿色", "es": "verde", "fr": "vert", "de": "grüne", "ja": "緑の"},
    "blue": {"it": "blu", "en": "blue", "zh": "蓝色", "es": "azul", "fr": "bleu", "de": "blaue", "ja": "青い"},
    "purple": {"it": "viola", "en": "purple", "zh": "紫色", "es": "morado", "fr": "violet", "de": "lila", "ja": "紫の"},
    "pink": {"it": "rosa", "en": "pink", "zh": "粉色", "es": "rosa", "fr": "rose", "de": "rosa", "ja": "ピンクの"},
    "white": {"it": "bianco", "en": "white", "zh": "白色", "es": "blanco", "fr": "blanc", "de": "weiße", "ja": "白い"},
    "grey": {"it": "grigio", "en": "grey", "zh": "灰色", "es": "gris", "fr": "gris", "de": "graue", "ja": "灰色の"},
    "black": {"it": "nero", "en": "black", "zh": "黑色", "es": "negro", "fr": "noir", "de": "schwarze", "ja": "黒い"},
}
T["cup_question"] = {
    "it": "Vedo il bicchiere {colour} nella zona {zone}. Lo indico con la punta. Confermi?",
    "en": "I see the {colour} cup in the {zone} zone. I will point at it. Proceed?",
    "zh": "我看到{colour}的纸杯在{zone}区域，我用指针指向它。确认吗？",
    "es": "Veo el vaso {colour} en la zona {zone}. Lo señalo con la punta. ¿Confirmas?",
    "fr": "Je vois le gobelet {colour} dans la zone {zone}. Je le montre avec la pointe. Tu confirmes ?",
    "de": "Ich sehe den {colour} Becher in Zone {zone}. Ich zeige mit der Spitze darauf. Bestätigst du?",
    "ja": "{colour}紙コップが{zone}ゾーンに見えます。先端で指します。よろしいですか？",
}
T["zone"] = {
    "sinistra": {"it": "sinistra", "en": "left", "zh": "左边", "es": "izquierda", "fr": "gauche", "de": "links", "ja": "左"},
    "centro": {"it": "centro", "en": "middle", "zh": "中间", "es": "centro", "fr": "centre", "de": "Mitte", "ja": "中央"},
    "destra": {"it": "destra", "en": "right", "zh": "右边", "es": "derecha", "fr": "droite", "de": "rechts", "ja": "右"},
}


T["dir"] = {
    "sinistra": {"it": "sinistra", "en": "left", "zh": "左", "es": "la izquierda", "fr": "la gauche", "de": "links", "ja": "左"},
    "destra": {"it": "destra", "en": "right", "zh": "右", "es": "la derecha", "fr": "la droite", "de": "rechts", "ja": "右"},
    "avanti": {"it": "avanti", "en": "forward", "zh": "前", "es": "adelante", "fr": "l'avant", "de": "vorne", "ja": "前"},
    "indietro": {"it": "indietro", "en": "back", "zh": "后", "es": "atrás", "fr": "l'arrière", "de": "hinten", "ja": "後ろ"},
    "su": {"it": "l'alto", "en": "up", "zh": "上", "es": "arriba", "fr": "le haut", "de": "oben", "ja": "上"},
    "giu": {"it": "il basso", "en": "down", "zh": "下", "es": "abajo", "fr": "le bas", "de": "unten", "ja": "下"},
    "piu": {"it": "verso positivo", "en": "positive direction", "zh": "正方向", "es": "sentido positivo",
            "fr": "sens positif", "de": "positive Richtung", "ja": "正方向"},
    "meno": {"it": "verso negativo", "en": "negative direction", "zh": "负方向", "es": "sentido negativo",
             "fr": "sens négatif", "de": "negative Richtung", "ja": "負方向"},
}
T["joint"] = {
    "base": {"it": "la base", "en": "the base", "zh": "底座", "es": "la base", "fr": "la base", "de": "die Basis", "ja": "ベース"},
    "spalla": {"it": "la spalla", "en": "the shoulder", "zh": "肩关节", "es": "el hombro", "fr": "l'épaule",
               "de": "die Schulter", "ja": "肩"},
    "gomito": {"it": "il gomito", "en": "the elbow", "zh": "肘关节", "es": "el codo", "fr": "le coude",
               "de": "den Ellbogen", "ja": "肘"},
    "polso1": {"it": "il polso 1", "en": "wrist 1", "zh": "手腕1", "es": "la muñeca 1", "fr": "le poignet 1",
               "de": "Handgelenk 1", "ja": "手首1"},
    "polso2": {"it": "il polso 2", "en": "wrist 2", "zh": "手腕2", "es": "la muñeca 2", "fr": "le poignet 2",
               "de": "Handgelenk 2", "ja": "手首2"},
    "polso3": {"it": "il polso 3", "en": "wrist 3", "zh": "手腕3", "es": "la muñeca 3", "fr": "le poignet 3",
               "de": "Handgelenk 3", "ja": "手首3"},
}
T["move_question"] = {
    "it": "Sposto la punta di {amount} cm verso {dir}. Confermi?",
    "en": "I will move the tip {amount} cm {dir}. Proceed?",
    "zh": "我将把指针向{dir}移动 {amount} 厘米。确认吗？",
    "es": "Muevo la punta {amount} cm hacia {dir}. ¿Confirmas?",
    "fr": "Je déplace la pointe de {amount} cm vers {dir}. Tu confirmes ?",
    "de": "Ich bewege die Spitze {amount} cm nach {dir}. Bestätigst du?",
    "ja": "先端を{dir}へ{amount}センチ動かします。よろしいですか？",
}
T["rotate_question"] = {
    "it": "Ruoto {joint} di {amount} gradi ({dir}). Confermi?",
    "en": "I will rotate {joint} by {amount} degrees ({dir}). Proceed?",
    "zh": "我将把{joint}向{dir}转 {amount} 度。确认吗？",
    "es": "Giro {joint} {amount} grados ({dir}). ¿Confirmas?",
    "fr": "Je tourne {joint} de {amount} degrés ({dir}). Tu confirmes ?",
    "de": "Ich drehe {joint} um {amount} Grad ({dir}). Bestätigst du?",
    "ja": "{joint}を{dir}に{amount}度回します。よろしいですか？",
}
T["touch_question"] = {
    "it": "Vedo il bicchiere {colour} a X {x} mm, Y {y} mm. Vado sopra, scendo piano fino al bordo e risalgo. Confermi?",
    "en": "I see the {colour} cup at X {x} mm, Y {y} mm. I will go above it, descend slowly to the rim and come back up. "
          "Proceed?",
    "zh": "我看到{colour}的纸杯，位置 X {x} 毫米、Y {y} 毫米。我先移到它上方，再慢慢下降碰到杯口，然后抬起。确认吗？",
    "es": "Veo el vaso {colour} en X {x} mm, Y {y} mm. Voy encima, bajo despacio hasta el borde y subo. ¿Confirmas?",
    "fr": "Je vois le gobelet {colour} à X {x} mm, Y {y} mm. Je vais au-dessus, je descends doucement jusqu'au bord et "
          "je remonte. Tu confirmes ?",
    "de": "Ich sehe den {colour} Becher bei X {x} mm, Y {y} mm. Ich fahre darüber, senke langsam bis zum Rand und fahre "
          "wieder hoch. Bestätigst du?",
    "ja": "{colour}紙コップが X {x} mm、Y {y} mm に見えます。上に移動し、縁までゆっくり下げて戻ります。よろしいですか？",
}
T["step_move"] = {
    "it": "punta {amount} cm verso {dir}", "en": "tip {amount} cm {dir}", "zh": "针尖向{dir} {amount} 厘米",
    "es": "punta {amount} cm hacia {dir}", "fr": "pointe {amount} cm vers {dir}",
    "de": "Spitze {amount} cm nach {dir}", "ja": "先端を{dir}へ{amount}センチ",
}
T["step_rotate"] = {
    "it": "{joint} di {amount} gradi ({dir})", "en": "{joint} {amount} degrees ({dir})",
    "zh": "{joint}向{dir}转 {amount} 度", "es": "{joint} {amount} grados ({dir})",
    "fr": "{joint} de {amount} degrés ({dir})", "de": "{joint} um {amount} Grad ({dir})",
    "ja": "{joint}を{dir}に{amount}度",
}
T["batch_question"] = {
    "it": "{n} movimenti, un solo sì li esegue tutti in ordine: {steps}. Confermi?",
    "en": "{n} moves. One yes runs them all in order: {steps}. Proceed?",
    "zh": "共 {n} 步，确认一次就会按顺序全部做完：{steps}。确认吗？",
    "es": "{n} movimientos. Un solo sí los ejecuta todos en orden: {steps}. ¿Confirmas?",
    "fr": "{n} mouvements. Un seul oui les fait tous dans l'ordre : {steps}. Tu confirmes ?",
    "de": "{n} Bewegungen. Ein Ja führt sie alle der Reihe nach aus: {steps}. Bestätigst du?",
    "ja": "{n}動作です。一度の確認で順番にすべて実行します：{steps}。よろしいですか？",
}
T["batch_amounts"] = {
    "it": "Servono {n} quantità, una per passo (centimetri o gradi), nello stesso ordine.",
    "en": "Say {n} amounts, one per step (centimetres or degrees), in the same order.",
    "zh": "这 {n} 步每一步都要有自己的数量（厘米或度），顺序和动作一致。",
    "es": "Hacen falta {n} cantidades, una por paso (centímetros o grados), en el mismo orden.",
    "fr": "Il faut {n} quantités, une par étape (centimètres ou degrés), dans le même ordre.",
    "de": "{n} Angaben nötig, eine pro Schritt (Zentimeter oder Grad), in derselben Reihenfolge.",
    "ja": "{n}ステップそれぞれに数量（センチか度）が必要です。順番を合わせてください。",
}
T["transfer_question"] = {
    "it": "Punto prima la zona {origin}, poi la zona {dest}, col polso. Nessuna pinza. Confermi?",
    "en": "I will point at {origin}, then at {dest}, with the wrist. No gripper. Proceed?",
    "zh": "用手腕先指到{origin}，再指到{dest}。没有夹爪，不抓。可以吗？",
    "es": "Señalo primero {origin} y luego {dest}, con la muñeca. Sin pinza. ¿Confirmas?",
    "fr": "Je montre d'abord {origin}, puis {dest}, avec le poignet. Pas de pince. Tu confirmes ?",
    "de": "Ich zeige zuerst auf {origin}, dann auf {dest}, mit dem Handgelenk. Kein Greifer. Bestätigst du?",
    "ja": "手首で先に{origin}、次に{dest}を指します。グリッパはないので掴みません。よろしいですか？",
}
T["how_much_cm"] = {
    "it": "Di quanti centimetri?", "en": "How many centimetres?",
    "zh": "移动多少厘米？", "es": "¿Cuántos centímetros?",
    "fr": "De combien de centimètres ?", "de": "Wie viele Zentimeter?",
    "ja": "何センチですか？",
}
T["cm_not_degrees"] = {
    "it": "Centimetri e 公分 spostano la punta, non ruotano un giunto. Dimmi la direzione, oppure i gradi.",
    "en": "Centimetres move the tip. They are not degrees. Say a direction, or say degrees.",
    "zh": "厘米和公分是移动针尖的距离，不是转动关节的角度。请说方向，或者说多少度。",
    "es": "Los centímetros mueven la punta, no giran una articulación. Di la dirección, o di los grados.",
    "fr": "Les centimètres déplacent la pointe, ils ne tournent pas un joint. Dis la direction, ou dis les degrés.",
    "de": "Zentimeter bewegen die Spitze, sie drehen kein Gelenk. Sag die Richtung, oder sag Grad.",
    "ja": "センチは先端を動かす距離で、関節の角度ではありません。方向を言うか、度数を言ってください。",
}
T["which_cup_colour"] = {
    "it": "Di che colore è il bicchiere? Rosso, arancione, giallo, verde, blu, viola, rosa, bianco, grigio o nero.",
    "en": "Which colour is the cup? Red, orange, yellow, green, blue, purple, pink, white, grey or black.",
    "zh": "纸杯是什么颜色？红、橙、黄、绿、蓝、紫、粉、白、灰，还是黑。",
    "es": "¿De qué color es el vaso? Rojo, naranja, amarillo, verde, azul, morado, rosa, blanco, gris o negro.",
    "fr": "De quelle couleur est le gobelet ? Rouge, orange, jaune, vert, bleu, violet, rose, blanc, gris ou noir.",
    "de": "Welche Farbe hat der Becher? Rot, orange, gelb, grün, blau, lila, rosa, weiß, grau oder schwarz.",
    "ja": "コップは何色ですか？赤、橙、黄、緑、青、紫、ピンク、白、灰、黒。",
}
T["out_of_scope"] = {
    "it": "Non posso farlo. Posso andare in una posa, indicare o toccare un bicchiere colorato, spostare la punta "
          "in centimetri, ruotare un giunto, sollevare, appoggiare o spingere un vassoio.",
    "en": "I can't do that. I can go to a pose, point at or touch a coloured cup, move the tip in centimetres, "
          "turn a joint, or lift, put down or push a tray.",
    "zh": "这句话我做不了。我可以去某个姿态、指向或碰到某个颜色的纸杯、按厘米移动针尖、按度数转一个关节，"
          "或者举起、放下、推动托盘。手机地址不在对话里。",
    "es": "Eso no puedo hacerlo. Puedo ir a una pose, señalar o tocar un vaso de color, mover la punta en "
          "centímetros, girar una articulación, o levantar, dejar o empujar una bandeja.",
    "fr": "Je ne peux pas faire ça. Je peux aller à une pose, montrer ou toucher un gobelet coloré, déplacer la "
          "pointe en centimètres, tourner un joint, ou soulever, poser ou pousser un bac.",
    "de": "Das kann ich nicht. Ich kann eine Pose anfahren, einen farbigen Becher zeigen oder berühren, die Spitze "
          "in Zentimetern bewegen, ein Gelenk drehen, oder eine Schale heben, abstellen oder schieben.",
    "ja": "それはできません。姿勢へ移動、色付きコップを指すか触れる、先端をセンチで動かす、関節を度で回す、"
          "トレイを持ち上げる・置く・押す、ができます。",
}
T["corner_name"] = {
    "avanti_sinistra": {"it": "avanti sinistra", "en": "front left", "zh": "前左", "es": "delante izquierda",
                        "fr": "avant gauche", "de": "vorne links", "ja": "前左"},
    "avanti_destra": {"it": "avanti destra", "en": "front right", "zh": "前右", "es": "delante derecha",
                      "fr": "avant droite", "de": "vorne rechts", "ja": "前右"},
    "indietro_destra": {"it": "indietro destra", "en": "back right", "zh": "后右", "es": "detrás derecha",
                        "fr": "arrière droite", "de": "hinten rechts", "ja": "後右"},
    "indietro_sinistra": {"it": "indietro sinistra", "en": "back left", "zh": "后左", "es": "detrás izquierda",
                          "fr": "arrière gauche", "de": "hinten links", "ja": "後左"},
}
T["corner_question"] = {
    "it": "Alzo la punta di {lift} mm, la porto all'angolo {n} ({name}, X {dx} mm, Y {dy} mm rispetto a dov'è ora) "
          "e la riabbasso. Confermi?",
    "en": "I will lift the tip {lift} mm, move it to corner {n} ({name}, X {dx} mm, Y {dy} mm from where it is now) "
          "and lower it again. Proceed?",
    "zh": "针尖会先抬起 {lift} 毫米，移到第 {n} 个角（{name}，相对现在的位置 X {dx} 毫米、Y {dy} 毫米），再降回原来的高度。确认吗？",
    "es": "Levanto la punta {lift} mm, la llevo a la esquina {n} ({name}, X {dx} mm, Y {dy} mm desde donde está) "
          "y la bajo otra vez. ¿Confirmas?",
    "fr": "Je lève la pointe de {lift} mm, je la mène au coin {n} ({name}, X {dx} mm, Y {dy} mm d'ici) "
          "et je la rabaisse. Tu confirmes ?",
    "de": "Ich hebe die Spitze {lift} mm, fahre zu Ecke {n} ({name}, X {dx} mm, Y {dy} mm von hier) "
          "und senke sie wieder. Bestätigst du?",
    "ja": "先端を {lift} mm 上げ、角 {n}（{name}、今の位置から X {dx} mm、Y {dy} mm）へ移して、元の高さに下ろします。よろしいですか？",
}
T["tip_not_vertical"] = {
    "it": "La punta non è verticale. Tienila rivolta in giù, poi ricalcola i quattro angoli.",
    "en": "The tip is not pointing straight down. Aim it down, then calculate the four corners again.",
    "zh": "针尖没有竖直朝下。先在示教器上把它调成朝下，再重新计算四个角。",
    "es": "La punta no está vertical. Apúntala hacia abajo y vuelve a calcular las cuatro esquinas.",
    "fr": "La pointe n'est pas verticale. Oriente-la vers le bas, puis recalcule les quatre coins.",
    "de": "Die Spitze zeigt nicht senkrecht nach unten. Richte sie nach unten und berechne die vier Ecken neu.",
    "ja": "先端が真下を向いていません。下に向けてから、四つの角を計算し直してください。",
}
T["area_too_small"] = {
    "it": "A questa altezza, con la punta verticale, quasi non c'è area raggiungibile. Abbassa la punta sul tavolo e riprova.",
    "en": "At this height, with the tip straight down, there is almost no reachable area. Lower the tip onto the table and try again.",
    "zh": "在这个高度，针尖竖直朝下时几乎挪不开。把针尖放到桌面上，再计算一次。",
    "es": "A esta altura, con la punta vertical, casi no hay área alcanzable. Baja la punta a la mesa y prueba otra vez.",
    "fr": "À cette hauteur, pointe verticale, il n'y a presque pas de zone atteignable. Pose la pointe sur la table et réessaie.",
    "de": "Auf dieser Höhe, Spitze senkrecht, ist fast nichts erreichbar. Setz die Spitze auf den Tisch und versuch es noch einmal.",
    "ja": "この高さで先端を真下に向けると、届く範囲がほとんどありません。先端を机まで下ろして、もう一度計算してください。",
}
T["cup_place"] = {
    "it": "{n} bicchieri: {left}° da sinistra, {front}° dal davanti",
    "en": "{n} cups: {left} from the left, {front} from the front",
    "zh": "共 {n} 个：从左数第 {left} 个，从前数第 {front} 个",
    "es": "{n} vasos: {left}º desde la izquierda, {front}º desde delante",
    "fr": "{n} gobelets : {left}ᵉ en partant de la gauche, {front}ᵉ en partant de l'avant",
    "de": "{n} Becher: {left}. von links, {front}. von vorne",
    "ja": "{n}個中、左から{left}番目、前から{front}番目",
}
T["cup_place_image"] = {
    "it": "{n} bicchieri nell'immagine: {left}° da sinistra. Davanti e dietro dopo il segno sul tavolo.",
    "en": "{n} cups in the picture: {left} from the left. Front and back need the printed mark first.",
    "zh": "画面里共 {n} 个，从左数第 {left} 个。桌面标记标定之后才能分出前后。",
    "es": "{n} vasos en la imagen: {left}º desde la izquierda. Delante y detrás requieren la marca impresa.",
    "fr": "{n} gobelets dans l'image : {left}ᵉ depuis la gauche. Avant et arrière après la marque imprimée.",
    "de": "{n} Becher im Bild: {left}. von links. Vorne und hinten erst nach der gedruckten Marke.",
    "ja": "画面に{n}個、左から{left}番目。前後は印刷したマークを記録してから分かります。",
}
T["cup_layout"] = {
    "it": "Da sinistra a destra: {lr}. Dal davanti al dietro: {fb}.",
    "en": "Left to right: {lr}. Front to back: {fb}.",
    "zh": "从左到右：{lr}。从前到后：{fb}。",
    "es": "De izquierda a derecha: {lr}. De delante hacia atrás: {fb}.",
    "fr": "De gauche à droite : {lr}. De l'avant vers l'arrière : {fb}.",
    "de": "Von links nach rechts: {lr}. Von vorne nach hinten: {fb}.",
    "ja": "左から右：{lr}。前から後ろ：{fb}。",
}
T["cup_layout_image"] = {
    "it": "Nell'immagine da sinistra a destra: {lr}. Il segno stampato serve per il davanti e il dietro.",
    "en": "In the picture, left to right: {lr}. The printed mark is needed for front and back.",
    "zh": "画面从左到右：{lr}。要分出前后，需要先标定桌面标记。",
    "es": "En la imagen, de izquierda a derecha: {lr}. La marca impresa hace falta para delante y detrás.",
    "fr": "Dans l'image, de gauche à droite : {lr}. La marque imprimée sert pour l'avant et l'arrière.",
    "de": "Im Bild von links nach rechts: {lr}. Die gedruckte Marke braucht es für vorne und hinten.",
    "ja": "画面の左から右：{lr}。前後は印刷したマークを記録してから分かります。",
}
T["cups_same_colour"] = {
    "it": "Vedo {n} bicchieri {colour}: {where}. Quale?",
    "en": "I see {n} {colour} cups: {where}. Which one?",
    "zh": "看到 {n} 个{colour}纸杯：{where}。要碰哪一个？",
    "es": "Veo {n} vasos {colour}: {where}. ¿Cuál?",
    "fr": "Je vois {n} gobelets {colour} : {where}. Lequel ?",
    "de": "Ich sehe {n} {colour} Becher: {where}. Welchen?",
    "ja": "{colour}のコップが{n}個あります：{where}。どれに触れますか？",
}
T["corners_first"] = {
    "it": "Prima calcola i quattro angoli, con la punta ferma sul tavolo.",
    "en": "Calculate the four corners first, with the tip resting on the table.",
    "zh": "请先让针尖停在桌面上，再计算四个角。",
    "es": "Primero calcula las cuatro esquinas, con la punta quieta sobre la mesa.",
    "fr": "Calcule d'abord les quatre coins, la pointe posée sur la table.",
    "de": "Berechne zuerst die vier Ecken, die Spitze ruht auf dem Tisch.",
    "ja": "先に先端を机に置いて、四つの角を計算してください。",
}
T["corner_too_far"] = {
    "it": "Quell'angolo è troppo lontano dalla posizione attuale. Ricalcola i quattro angoli da dove si trova ora la punta.",
    "en": "That corner is too far from where the arm is now. Calculate the four corners again from the current tip.",
    "zh": "这个角离现在的位置太远。请在针尖当前的位置重新计算四个角。",
    "es": "Esa esquina está demasiado lejos. Vuelve a calcular las cuatro esquinas desde la punta actual.",
    "fr": "Ce coin est trop loin. Recalcule les quatre coins depuis la pointe actuelle.",
    "de": "Diese Ecke ist zu weit weg. Berechne die vier Ecken von der aktuellen Spitze neu.",
    "ja": "その角は今の位置から遠すぎます。今の先端位置から四つの角を計算し直してください。",
}
T["how_much_deg"] = {
    "it": "Di quanti gradi? (massimo {max})", "en": "How many degrees? (max {max})",
    "zh": "转多少度？（最多 {max} 度）", "es": "¿Cuántos grados? (máximo {max})",
    "fr": "De combien de degrés ? (maximum {max})", "de": "Wie viel Grad? (höchstens {max})",
    "ja": "何度ですか？（最大 {max}）",
}


T["marker_need"] = {
    "it": "Tavolo non calibrato. Stampa il segno, appoggia la punta al centro e poi sulla freccia.",
    "en": "The table is not calibrated. Print the mark, then put the tip on the centre and on the arrow.",
    "zh": "桌面还没标定。先打印标记，针尖点在中心，再点在箭头上。",
    "es": "La mesa no está calibrada. Imprime la marca y pon la punta en el centro y luego en la flecha.",
    "fr": "La table n'est pas calibrée. Imprime la marque, puis pose la pointe au centre et sur la flèche.",
    "de": "Der Tisch ist nicht kalibriert. Druck die Marke und setz die Spitze auf die Mitte, dann auf den Pfeil.",
    "ja": "机はまだ校正されていません。マークを印刷し、先端を中心、次に矢印に当ててください。",
}
T["marker_not_seen"] = {
    "it": "Il segno non è nell'immagine. Avvicina la fotocamera, poi ripeti il punto.",
    "en": "The mark is not in the picture. Bring the camera closer, then record the point again.",
    "zh": "画面里没有标记。让相机看到它，再点一次。",
    "es": "La marca no está en la imagen. Acerca la cámara y vuelve a registrar el punto.",
    "fr": "La marque n'est pas dans l'image. Rapproche la caméra, puis enregistre le point.",
    "de": "Die Marke ist nicht im Bild. Kamera näher heran, dann den Punkt noch einmal aufnehmen.",
    "ja": "マークが画面にありません。カメラに映してから、もう一度記録してください。",
}
T["marker_size"] = {
    "it": "La distanza tra centro e freccia non è metà del quadrato da {mm} mm. Misura il quadrato nero stampato.",
    "en": "The centre and the arrow are not half of the {mm} mm square apart. Measure the printed black square.",
    "zh": "中心到箭头的距离不是 {mm} 毫米方块的一半。请量一下印出来的黑方块边长。",
    "es": "El centro y la flecha no están a la mitad del cuadrado de {mm} mm. Mide el cuadrado negro impreso.",
    "fr": "Le centre et la flèche ne sont pas à la moitié du carré de {mm} mm. Mesure le carré noir imprimé.",
    "de": "Mitte und Pfeil liegen nicht um die Hälfte des {mm}-mm-Quadrats auseinander. Miss das gedruckte schwarze Quadrat.",
    "ja": "中心と矢印の距離が {mm} mm の正方形の半分ではありません。印刷した黒い正方形を測ってください。",
}
T["marker_height"] = {
    "it": "I due punti non sono alla stessa altezza. La punta deve toccare il tavolo in entrambi.",
    "en": "The two points are not at the same height. The tip has to rest on the table for both.",
    "zh": "两个点不在同一高度。两次都要让针尖碰到桌面。",
    "es": "Los dos puntos no están a la misma altura. La punta tiene que tocar la mesa en ambos.",
    "fr": "Les deux points ne sont pas à la même hauteur. La pointe doit toucher la table les deux fois.",
    "de": "Die zwei Punkte sind nicht auf derselben Höhe. Die Spitze muss beide Male den Tisch berühren.",
    "ja": "2点が同じ高さにありません。どちらも先端を机に付けてください。",
}
T["marker_orient"] = {
    "it": "L'orientamento della punta è cambiato. Tienila verticale e sposta solo la posizione.",
    "en": "The tip orientation changed. Keep it straight down and move only its position.",
    "zh": "针尖的朝向变了。保持竖直朝下，只移动位置。",
    "es": "Cambió la orientación de la punta. Déjala vertical y mueve solo la posición.",
    "fr": "L'orientation de la pointe a changé. Garde-la verticale et ne déplace que la position.",
    "de": "Die Ausrichtung der Spitze hat sich geändert. Senkrecht lassen und nur die Position ändern.",
    "ja": "先端の向きが変わりました。真下を向けたまま、位置だけ動かしてください。",
}
T["outside_table"] = {
    "it": "Quel punto è fuori dal tavolo che il braccio può toccare. Sposta il bicchiere dentro quell'area.",
    "en": "That spot is outside the table area this arm can touch. Move the cup inside that area.",
    "zh": "这个位置超出了这台手臂在桌面上够得到的范围。把纸杯放到这片桌面里。",
    "es": "Ese punto está fuera del área de la mesa que el brazo puede tocar. Mueve el vaso dentro.",
    "fr": "Ce point est hors de la zone de table que le bras peut toucher. Déplace le gobelet dedans.",
    "de": "Dieser Punkt liegt außerhalb der Tischfläche, die der Arm erreichen kann. Stell den Becher hinein.",
    "ja": "その位置は、この腕が机の上で届く範囲の外です。コップをその範囲の中に置いてください。",
}
T["marker_tilt"] = {
    "it": "La punta non è verticale. Rivolgila in giù prima di registrare il punto.",
    "en": "The tip is not pointing straight down. Aim it down before recording the point.",
    "zh": "针尖没有竖直朝下。先在示教器上调成朝下，再记录这个点。",
    "es": "La punta no está vertical. Apúntala hacia abajo antes de registrar el punto.",
    "fr": "La pointe n'est pas verticale. Oriente-la vers le bas avant d'enregistrer le point.",
    "de": "Die Spitze zeigt nicht senkrecht nach unten. Richte sie nach unten, bevor du den Punkt aufnimmst.",
    "ja": "先端が真下を向いていません。記録する前に下へ向けてください。",
}


def word(table: str, key: str, lang: str) -> str:
    row = T[table].get(key)
    return key if row is None else row.get(lang, row["en"])


def t(key: str, lang: str, **kw: object) -> str:
    table = T[key]
    return table.get(lang, table["en"]).format(**kw)
