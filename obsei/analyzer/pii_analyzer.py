import logging
from typing import Any, Dict, List, Optional

from presidio_analyzer import AnalyzerEngine, EntityRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer.entities import AnonymizerConfig
from pydantic import BaseModel, Field, PrivateAttr

from obsei.analyzer.base_analyzer import AnalyzerRequest, AnalyzerResponse, BaseAnalyzer, BaseAnalyzerConfig

logger = logging.getLogger(__name__)


class PresidioModelConfig(BaseModel):
    lang_code: Optional[str] = Field("en")
    model_name: Optional[str] = Field("en_core_web_lg")


class PresidioEngineConfig(BaseModel):
    nlp_engine_name: Optional[str] = Field("spacy")
    models: Optional[List[PresidioModelConfig]] = None

    def __init__(self, **data: Any):
        super().__init__(**data)

        if not self.models or len(self.models) == 0:
            self.models = [PresidioModelConfig()]


class PresidioAnonymizerConfig(AnonymizerConfig, BaseModel):
    def __init__(self, anonymizer_name: str, params: Optional[Dict[str, Any]] = None):
        super().__init__(anonymizer_name=anonymizer_name, params=params)

    class Config:
        arbitrary_types_allowed = True


class PresidioPIIAnalyzerConfig(BaseAnalyzerConfig):
    TYPE: str = "PresidioPII"
    # To find more details refer https://microsoft.github.io/presidio/anonymizer/
    anonymizers_config: Optional[Dict[str, PresidioAnonymizerConfig]] = None
    # To see list of supported entities refer https://microsoft.github.io/presidio/supported_entities/
    # By default it will search for all the supported entities
    entities: Optional[List[str]] = None
    analyze_only: Optional[bool] = False
    replace_original_text: Optional[bool] = True
    # Whether the analysis decision process steps returned in the response
    return_decision_process: Optional[bool] = False


class PresidioPIIAnalyzer(BaseAnalyzer):
    _analyzer: AnalyzerEngine = PrivateAttr()
    _anonymizer: AnonymizerEngine = PrivateAttr()
    TYPE: str = "PresidioPII"
    engine_config: Optional[PresidioEngineConfig] = None
    # To see list of supported entities refer https://microsoft.github.io/presidio/supported_entities/
    # To add customer recognizers refer https://microsoft.github.io/presidio/analyzer/adding_recognizers/
    entity_recognizers: Optional[List[EntityRecognizer]] = None
    # To find more details refer https://microsoft.github.io/presidio/anonymizer/
    anonymizers_config: Optional[Dict[str, AnonymizerConfig]] = None

    def __init__(self, **data: Any):
        super().__init__(**data)

        if not self.engine_config:
            self.engine_config = PresidioEngineConfig()

        # If spacy engine then load Spacy models and select languages
        languages = []
        for model_config in self.engine_config.models:
            languages.append(model_config.lang_code)

            # Check SpacyNlpEngine.engine_name
            if self.engine_config.nlp_engine_name == "spacy":
                try:
                    import spacy
                    spacy.load(model_config.model_name)
                    logger.info(f"Spacy model {model_config.model_name} is already downloaded")
                except:
                    logger.warning(f"Spacy model {model_config.model_name} is not downloaded")
                    logger.warning(f"Downloading spacy model {model_config.model_name}, it might take some time")
                    from spacy.cli import download
                    download(model_config.model_name)

        # Create NLP engine based on configuration
        provider = NlpEngineProvider(nlp_configuration=self.engine_config.dict())
        nlp_engine = provider.create_engine()

        # Pass the created NLP engine and supported_languages to the AnalyzerEngine
        self._analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            supported_languages=languages
        )

        # self._analyzer.registry.load_predefined_recognizers()
        if self.entity_recognizers:
            for entity_recognizer in self.entity_recognizers:
                self._analyzer.registry.add_recognizer(entity_recognizer)

        # Initialize the anonymizer with logger
        self._anonymizer = AnonymizerEngine()

    def analyze_input(
        self,
        source_response_list: List[AnalyzerRequest],
        analyzer_config: PresidioPIIAnalyzerConfig,
        language: Optional[str] = "en",
        **kwargs
    ) -> List[AnalyzerResponse]:
        analyzer_output: List[AnalyzerResponse] = []

        for source_response in source_response_list:
            analyzer_result = self._analyzer.analyze(
                text=source_response.processed_text,
                entities=analyzer_config.entities,
                return_decision_process=analyzer_config.return_decision_process,
                language=language
            )

            anonymized_result = None
            if not analyzer_config.analyze_only:
                anonymizers_config = analyzer_config.anonymizers_config or self.anonymizers_config

                if source_response.processed_text is not None and len(source_response.processed_text) > 0:
                    anonymized_result = self._anonymizer.anonymize(
                        text=source_response.processed_text,
                        anonymizers_config=anonymizers_config,
                        analyzer_results=analyzer_result
                    )

            if analyzer_config.replace_original_text and anonymized_result is not None:
                text = anonymized_result.text
            else:
                text = source_response.processed_text

            analyzer_output.append(
                AnalyzerResponse(
                    processed_text=text,
                    meta=source_response.meta,
                    segmented_data={
                        "analyzer_result": [vars(result) for result in analyzer_result],
                        "anonymized_result": None if not anonymized_result else [
                            vars(item) for item in anonymized_result.items
                        ],
                        "anonymized_text": None if not anonymized_result else anonymized_result.text
                    },
                    source_name=source_response.source_name,
                )
            )

        return analyzer_output
