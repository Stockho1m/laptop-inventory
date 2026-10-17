# Сторонние файлы

## zxing.min.js

- Библиотека: [@zxing/library](https://github.com/zxing-js/library), версия **0.21.3**
- Источник: `https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js`
- Лицензия: Apache-2.0
- Размер: 328 КБ

Декодер штрих-кодов и QR-кодов для встроенного сканера (`web/app.js`).
Лежит в репозитории, а не подключается с CDN: Mini App не должен делать
внешних запросов, иначе скан перестанет работать без интернета на стороне
клиента и появится лишняя зависимость от чужого хостинга.

Обновление: скачать файл по ссылке выше с нужной версией, заменить
`zxing.min.js` и проверить сканирование на телефоне, обязательно на iPhone:
там нет системного `BarcodeDetector`, и ZXing — единственный декодер.

Из глобального объекта `ZXing` используются `MultiFormatReader`,
`HTMLCanvasElementLuminanceSource`, `BinaryBitmap`, `HybridBinarizer`,
`DecodeHintType`, `BarcodeFormat` и исключения `NotFoundException`,
`ChecksumException`, `FormatException`. При смене мажорной версии проверь,
что эти имена на месте.

Готовый `BrowserMultiFormatReader.decodeFromStream` намеренно не используется:
на iOS он создаёт холст 0×0, если видео начало играть раньше, чем сообщило свой
размер, и молча останавливается на первой же такой ошибке. Кадры рисует и
обрезает по рамке сам `web/app.js` (функция `runZxingLoop`).
