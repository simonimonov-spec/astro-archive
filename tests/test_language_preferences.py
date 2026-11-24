# Beta 1.3 UI · language preference persistence.
from tempfile import TemporaryDirectory
from unittest import TestCase

from Backend.Foundation.runtime import Settings, Store


class LanguagePreferencesTests(TestCase):
    def test_new_profile_starts_in_english_and_reopens_in_saved_language(self):
        with TemporaryDirectory() as folder:
            store = Store(folder)
            settings = Settings(store)
            self.assertEqual(settings.value['preferences']['languageId'], 'en')
            for language in ('bg', 'ja'):
                settings.value['preferences']['languageId'] = language
                settings.save()
                self.assertEqual(Settings(store).value['preferences']['languageId'], language)

    def test_older_profile_without_language_uses_english(self):
        with TemporaryDirectory() as folder:
            store = Store(folder)
            settings = Settings(store)
            del settings.value['preferences']['languageId']
            settings.value['preferences']['themeId'] = 'builtin.red'
            settings.save()
            preferences = Settings(store).value['preferences']
            self.assertEqual(preferences['languageId'], 'en')
            self.assertEqual(preferences['themeId'], 'builtin.red')
