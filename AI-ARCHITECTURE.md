# ResNet FITS оценител · архитектура 0.9

## Модел и пиксели

`resnet_model.py`: compact ResNet-18 BasicBlock [2,2,2,2], ширина 16,
3×3 stride-1 stem, GroupNorm, без начален max-pool. Това е собствена
модификация на residual архитектурата, без torchvision или pretrained тегла.
Първоизточник: https://arxiv.org/abs/1512.03385 .

`visual_data.py`: readonly FITS sections, BSCALE/BZERO/BLANK, extensions и
compressed images; raw CFA/mono или mean RGB. Native 256² области със stride
224 и отделен обзор. Float64 pedestal subtraction → signed asinh/high-pass/
valid mask → float32. Пълното native покритие се проверява преди инференция.

Mean/max pooling на визуалните признаци се слива с нормализирани физически
измервания, наличностите им, analyzer scores и контекст. FWHM/HFR/форма/
брой звезди/шум/фон/градиент остават измервания. ResNet рисковете са estimated.
Няма head „наличие на обект = дефект“. Обектите са морфологични кандидати.

## Процеси

1. Приложението измерва и извършва инференция; никога не обучава.
2. `ai-training` е bridge за quick labels, статус и отваряне на Trainer.
3. Отделният `Plugins/frame-inspector-ai/Trainer` обучава ResNet. Train използва въртящи се
   native области с overview за ограничена памет. Validation/test/inference
   използват всички области. Групите и frame hashes са disjoint.
4. Validation избира checkpoint; независим test се изпълнява след избора.
5. Candidate се предава през API и остава неактивен. Activate и rollback
   са отделни операции с изрично потвърждение и audit.

## Версии и обмен

- Result: `astro-ai-result-v1`, engine.kind `neural` + architecture за ResNet;
  без използваем ResNet: `heuristic`, `nnAvailable=false`.
- Model: `astro-ai-resnet-v1`, с `ai-resnet.schema.json` и допълнителни
  числови/групови/weights проверки. MLP договорът остава само за legacy четене.
- Pixel preprocessing: `astro-fits-native-asinh-v1`.
- Measurement input: `astro-ai-visual-measurements-v1`, фиксиран ред + mask.
- Dataset ID, version, hash и freeze остават задължителни за обучение.
- `frame.visual` добавя scoped pixel обмен по ID/hash. Числовите weights NPZ
  се предават на chunks и се проверяват преди install. Няма pickle модел.
- HTTP/1.1 връзката се използва повторно; няма повторение на мутации при
  неясен транспортен резултат. Няма външни връзки или автоматичен download.

## Приемане

Проверките са изпълнени наново върху текущия код. Включват действителен
localhost host и отделен Trainer процес, със синтетични FITS и временен
архивен профил. Предишни доклади не са доказателство за тази реализация.
Реална астрономическа точност, мащабна продължителна сесия и пълен визуален
преглед остават отворени. Подробности: `README.md`, `SAFETY-ACCEPTANCE.md`.

`ARCHITECTURE-MLP-HISTORICAL.md` описва предходния проект, не текущия модел.
