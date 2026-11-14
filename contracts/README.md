# AI договори · версия 1

Текущият модел е `astro-ai-resnet-v1` (`ai-resnet.schema.json`). Новият
оценител не изпълнява MLP; `ai-model.schema.json` остава legacy договор.
ResNet metadata се проверява по JSON схемата и допълнително за split/metrics,
числови масиви и SHA-256 на weights.npz. Pixel обменът е
`astro-fits-native-asinh-v1`, а входните измервания —
`astro-ai-visual-measurements-v1`. Result v1 запазва engine.kind `neural`
и добавя architecture за конкретния ResNet. Evidence може да посочи native
или analysis_sample координати.

Тези JSON договори се използват от новия оценител. Резултатът и MLP моделът
се проверяват по включените схеми; примерите и dataset manifest се валидират
от dataset store. Неизвестните event стойности са `null`. Основните осем
label полета остават задължителни; `noise`, `objectRisk`, `obstruction`, `defocus`
са съвместими незадължителни добавки. `elongation` означава елипсовидност, а
`defocus` — отделен ръчен риск от разфокусиране (0–1 или null). В новото ResNet
обучение `risk.stars` използва максимума от наличните elongation/defocus етикети.

| Файл | Формат | Предназначение |
|---|---|---|
| `ai-result.schema.json` | `astro-ai-result-v1` | Структуриран резултат от оценителя |
| `ai-model.schema.json` | `astro-ai-mlp-v1` | MLP тегла, bias-ове, входове и изходи |
| `ai-example.schema.json` | `astro-ai-example-v1` | Един обучителен пример с етикети |
| `ai-dataset.schema.json` | `astro-ai-dataset-v1` | Manifest на замразен dataset |

## Правила за версии

- Полето `schema`/`format` е задължително и не се променя при съвместими добавки.
- Нова несъвместима промяна вдига версията: `astro-ai-result-v2`, `astro-ai-mlp-v2` и т.н.
- Старите версии остават четими; никой модел не се активира автоматично при миграция.
- `featureSchema` в модела трябва да съвпада с активната схема на признаците.
- Размерите на `mean`, `scale`, теглата и bias-овете се проверяват допълнително при зареждане; JSON Schema не може да наложи всички матрични зависимости.

## Валидация

Схемите са JSON Schema draft 2020-12. Валидацията в програмата се изпълнява преди запис или активиране. Липсващо поле, непознат enum, извън диапазон или несъвпадащ размер се отказва с `invalid_request`.

## Ограничения

- Схемите описват договор, не гарантират точност.
- `bbox` е `[x, y, width, height]` в пиксели на анализирания кадър.
- `quality` е 0..1, по-високо е по-добре; `risk` е 0..1, по-високо е по-лошо.
- `mag/arcsec²` не е част от задължителните полета, защото изисква photometric zeropoint.
