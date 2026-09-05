import { createContext, useContext, useState } from 'react'
import enumLabels from '../enumLabels.json'

const translations = {
  sl: {
    // Nav
    myAnalyses: 'Moje analize',
    newAnalysis: 'Nova analiza',
    login: 'Prijava',
    register: 'Registracija',
    logout: 'Odjava',

    // HomePage
    completedAnalyses: 'Opravljene analize',
    analysisCount: (n) => `${n} ${n === 1 ? 'analiza' : 'analiz'} v bazi`,
    searchPlaceholder: 'Iskanje po temi, govorcih...',
    search: 'Iskanje',
    noAnalyses: 'Ni shranjenih analiz',
    startFirst: 'Začni prvo analizo',
    all: 'Vse',
    soloOnly: 'Solo',
    debateOnly: 'Razprava',
    noTopic: 'Brez teme',
    speakers: 'Govorci',
    processing: 'obdelave',
    deleteConfirm: 'Ali ste prepričani, da želite izbrisati to analizo?',
    delete: 'Izbriši',

    // DebateViewPage
    back: 'Nazaj',
    analysis: 'Analiza',
    whatDoesAnalysisMean: 'Kaj pomeni analiza?',
    youtubeVideo: 'YouTube video',
    timeline: 'Časovnica',
    factCheck: 'Preverjanje dejstev',
    report: 'Poročilo',
    analysisNotFound: 'Analiza ni najdena',
    backToList: 'Nazaj na seznam',
    // PDF export
    exportPdf: 'Izvozi PDF',
    exporting: 'Izvažam…',
    diarizationNote: 'Govorci so samodejno zaznani — če je kateri napačno pripisan, ga popravi prek „Uredi".',
    fallacyCat_formal_desc: 'Napaka v obliki sklepanja — prepoznavna brez poznavanja vsebine (npr. potrditev posledice).',
    fallacyCat_informal_desc: 'Napaka, odvisna od konteksta: ista poteza je lahko drugje povsem legitimna.',
    fallacyCat_weak_reasoning_desc: 'Sklep sledi, a je močnejši, kot ga dokazi dopuščajo — preohlapno sklepanje.',
    moderatorTitle: 'Moderator',
    moderatorQuestions: 'zastavljenih vprašanj',
    moderatorPressed: 'Bolj pritiskal na',
    moderatorShowQuestions: 'Pokaži vprašanja moderatorja',
    moderatorNotScored: 'Moderator ni debater: ni ocenjen in ne nastopa med govorci. Prikaz je zgolj informativen.',

    // FactCheckPanel
    checked: 'Preverjeno',
    verdictsBySpeaker: 'Razsodbe po govorcih',
    true_: 'Resnično',
    false_: 'Neresnično',
    explanation: 'Obrazložitev',
    context: 'Kontekst',
    sources: 'Viri',
    sourceVerdicts: 'Kaj pove posamezen vir',
    sourceCount: 'Virov',
    independentDomains: 'Neodvisnih domen',

    // Report tab
    reportNotAvailable: 'Poročilo ni na voljo',
    claimSpeaker: 'Govorec',
    reportOverview: 'Pregled poročila',
    totalClaims: 'Skupaj trditev',
    trueClaims: 'Resnične',
    falseClaims: 'Neresnične',
    partiallyTrue: 'Delno resnično',
    misleading: 'Zavajajoče',
    unverifiableShort: 'Nepreverljivo',

    // Verdicts
    TRUE: 'RESNIČNO',
    FALSE: 'NERESNIČNO',
    PARTIALLY_TRUE: 'DELNO RESNIČNO',
    MISLEADING: 'ZAVAJAJOČE',
    UNVERIFIABLE: 'NEPREVERJIVO',

    // LoginPage
    loginTitle: 'Prijava',
    loginSubtitle: 'Prijavi se za dostop do svojih analiz',
    usernameOrEmail: 'Uporabniško ime ali email',
    password: 'Geslo',
    loggingIn: 'Prijavljam...',
    loginButton: 'Prijava',
    noAccount: 'Nimaš računa?',
    loginError: 'Napaka pri prijavi',

    // RegisterPage
    registerTitle: 'Registracija',
    registerSubtitle: 'Ustvari račun za shranjevanje analiz',
    username: 'Uporabniško ime',
    usernamePlaceholder: 'npr. janez123',
    email: 'Email',
    emailPlaceholder: 'janez@primer.com',
    passwordPlaceholder: 'Vsaj 6 znakov',
    registering: 'Registriram...',
    createAccount: 'Ustvari račun',
    haveAccount: 'Že imaš račun?',
    registerError: 'Napaka pri registraciji',

    // AnalyzePage
    newAnalysisTitle: 'Nova analiza',
    newAnalysisSubtitle: 'Vnesi YouTube URL ali naloži video/audio datoteko za analizo',
    source: 'Vir',
    youtubeUrl: 'YouTube URL',
    videoLink: 'Povezava do videa',
    uploadFile: 'Naloži datoteko',
    fileFormats: 'MP3, MP4, WAV, ...',
    file: 'Datoteka',
    clickToSelect: 'Klikni za izbiro datoteke',
    debateTitle: 'Ime debate',
    optional: 'opcijsko',
    debateTitlePlaceholder: 'npr. Predsedniška debata 2024',
    debateTitleHint: 'Ime za lažje prepoznavanje analize. Če pustiš prazno, bo sistem uporabil temo iz analize.',
    analysisMode: 'Način analize',
    solo: 'Solo',
    soloDesc: 'En govorec — govor, intervju ali reakcijski video',
    debate: 'Razprava',
    debateDesc: 'Dva debaterja — ena na ena',
    speakerNames: 'Imena govorcev',
    speakerNamesPlaceholder: 'npr. Janez Novak, Ana Krajnc',
    speakerNamesHint: 'Loči z vejico. Zamenja SPEAKER_0, SPEAKER_1, ... v transkriptu. Če pustiš prazno, bo sistem avtomatsko identificiral govorce.',
    analysisLanguage: 'Jezik analize',
    slovenian: 'Slovenščina',
    slovenianDesc: 'Analiza v slovenščini',
    english: 'English',
    englishDesc: 'Analysis in English',
    submitting: 'Oddajam...',
    startAnalysis: 'Začni analizo',
    credits: 'Krediti',
    unlimited: 'neomejeno',
    noCreditsError: 'Nimaš kreditov za analizo. Kontaktiraj administratorja.',
    rateLimitError: 'Presežen limit zahtev. Počakaj 24 ur.',
    submitError: 'Napaka pri oddaji',
    analysisInProgress: 'Analiza v teku',
    waitForCurrentAnalysis: 'Počakaj, da se trenutna analiza konča',
    blockingHint: 'Nove analize ne moreš zagnati, dokler prejšnja ni končana. Ko bo gotova, se obrazec samodejno odklene.',
    currentStep: 'Trenutni korak',
    openCurrentAnalysis: 'Odpri trenutno analizo →',
    probingVideo: 'Preverjam dolžino videa...',
    probeFailedHint: 'Dolžine videa ni bilo mogoče zaznati — drsnik bo deloval ročno.',
    minutesShort: 'min',

    // JobStatusPage
    jobStatusTitle: 'Analiza v teku',
    jobIdLabel: 'ID naloge',
    jobStepLoading: 'Nalaganje konfiguracije',
    jobStepDownloading: 'Prenos avdia',
    jobStepUsing: 'Naložena datoteka',
    jobStepTranscribing: 'Transkripcija govora',
    jobStepFactChecking: 'Preverjanje dejstev',
    jobStepAnalyzing: 'Analiza argumentov',
    jobStepDone: 'Zaključeno',
    jobCompletedRedirect: 'Analiza zaključena! Preusmerjam...',
    jobFailedDefault: 'Analiza ni uspela',
    jobNotFound: 'Analiza ni bila najdena. Mogoče je potekla ali bila izbrisana.',
    jobNetworkError: 'Povezava s strežnikom je prekinjena. Osveži stran.',
    jobRetry: 'Poskusi znova',
    jobQueued: 'V čakalni vrsti...',
    jobProcessing: 'Obdelujem...',
    analysisRunning: 'Analiza teče',
    clickToView: 'Klikni za ogled',

    // TimeRangeSlider
    trimClip: 'Izreži del posnetka',
    resetTrim: 'Ponastavi',
    trimEnd: 'konec',
    trimLength: 'Dolžina',
    trimLengthLabel: 'Dolžina:',

    // DebateViewPage
    editDebate: 'Uredi',
    editDebateTitle: 'Uredi imena, argumente, povzetek',

    // HomePage errors
    loadDebatesError: 'Nalaganje analiz ni uspelo. Poskusi znova.',
    deleteDebateError: 'Brisanje ni uspelo.',

    // Error boundary
    errorBoundaryTitle: 'Nekaj je šlo narobe',
    errorBoundaryMessage: 'Osveži stran ali poskusi znova pozneje.',
    errorBoundaryReload: 'Osveži stran',

    // HomePage tutorial
    tutorialButton: 'Kako deluje?',
    tutorialTitle: 'Kako uporabljati Debate Analyzer',
    tutorialClose: 'Zapri',
    tutorialStep1Title: '1. Oddaj posnetek',
    tutorialStep1Text: 'Vnesi YouTube URL ali naloži audio/video datoteko. Izberi način analize (solo ali debata) in jezik.',
    tutorialStep2Title: '2. Počakaj na analizo',
    tutorialStep2Text: 'Sistem bo prepisal posnetek, identificiral govorce, izluščil argumente in preveril dejanske trditve z akademskimi viri.',
    tutorialStep3Title: '3. Preglej rezultate',
    tutorialStep3Text: 'Odpri analizo in preglej časovnico argumentov, preverjanje dejstev z viri in celostno poročilo s presojami za vsako trditev.',

    // AppGuideModal
    guideCloseLabel: 'Zapri pomoč',
    guideHowItWorks: 'Vodnik',
    guideTitle: 'Kako brati to analizo',
    guideClose: 'Zapri',

    // 1. Kaj app naredi
    guideWhatAppDoes: 'Kaj app naredi',
    guideWhatAppDoesText: 'Iz posnetka ali transkripta izlušči glavne argumente, premise, protiargumente, preveri dejanske trditve in pri debatah primerja govorce. Rezultat ni absolutna resnica — je strukturirana analiza, ki jo lahko po potrebi popraviš.',

    // 2. Kako brati argumente
    guideHowToReadNodes: 'Kako brati argumente',
    guideNodeLine1: 'Vsak okvirček je en argument — popolna veriga sklepanja (premise → razlog → zaključek).',
    guideNodeLine2: 'Ko ga odpreš, vidiš premise, oponentov odziv in povezane fact-checke.',
    guideNodeLine3: 'Barva in poudarki opozarjajo, kje je argument močan ali problematičen.',

    // 3. Razsodbe fact-checka
    guideVerdictsTitle: 'Razsodbe fact-checka',
    guideVerdictTrue: 'Točno',
    guideVerdictTrueDesc: 'Trditev je preverljivo točna z zanesljivimi viri.',
    guideVerdictPartial: 'Delno točno',
    guideVerdictPartialDesc: 'Večinoma točna trditev z manjšimi netočnostmi ali pridržki.',
    guideVerdictMisleading: 'Zavajajoče',
    guideVerdictMisleadingDesc: 'Vsebuje resnico, ovita v zavajajoč okvir, ki krivi vtis.',
    guideVerdictFalse: 'Napačno',
    guideVerdictFalseDesc: 'Trditev je v nasprotju z zanesljivimi dokazi.',
    guideVerdictUnverifiable: 'Nepreverljivo',
    guideVerdictUnverifiableDesc: 'Ni dovolj zanesljivih virov za sodbo (mnenje, predikcija, debatna pozicija).',

    // 4. Perspektiva virov
    guideSourcesTitle: 'Uravnoteženi viri',
    guideSourcesDesc: 'Pri vsaki trditvi iščemo vire iz različnih perspektiv. Če nekdo zagovarja katoliško stališče, pogledamo tudi katoliške vire — ne samo nasprotnih.',
    guideSourceAligned: 'Iz govorčeve tradicije',
    guideSourceNeutral: 'Neodvisni / mainstream',
    guideSourceOpposing: 'Iz nasprotnega tabora',

    // 5. Načini analize
    guideModesTitle: 'Načini analize',
    guideModeSoloTitle: 'Solo',
    guideModeSoloDesc: 'En primarni govorec — govor, intervju, predavanje ali reakcijski video.',
    guideModeDebateTitle: 'Razprava',
    guideModeDebateDesc: 'Debata ena na ena: natanko dva debaterja z nasprotujočima stališčema. Moderator se ne šteje med debaterja — zabeleži se posebej in ni ocenjen. Če posnetek vsebuje več kot dva debaterja, se analiza ustavi z opozorilom.',

    // 6. Edit
    guideEditTitle: 'Lahko urejaš analizo',
    guideEditDesc: 'Klikni "Uredi" zgoraj desno na strani analize. Lahko preimenuješ govorce, popraviš ali izbrišeš argumente, dodaš nove, urediš povzetek in temo.',

    // Footer disclaimer
    guideImportantNote: 'Dobro je vedeti',
    guideImportantNoteText: 'Analiza je AI-generirana in ima lahko napake. Preveri pomembne trditve sam, popravi argumente prek "Uredi", in ne vzemi nobene ocene kot dokončne sodbe.',

    // ArgumentNode
    argSpeaker: 'Govorec',
    argCloseLabel: 'Zapri podrobnosti argumenta',
    argPremises: 'Premise',
    argDerived: 'Izpeljan argument (sklep)',
    rerun: 'Ponovna analiza',
    recheck: 'Osveži vire',
    recheckRunning: 'Preverjam …',
    recheckTitle: 'Znova preveri dejstva nad isto analizo. Argumenti, zmote in zavrnitve ostanejo, osvežijo se viri in razsodbe.',
    recheckConfirm: 'Znova preverim dejstva te analize? Argumenti ostanejo nespremenjeni, osvežijo se samo viri in razsodbe. Poraba: en kredit.',
    recheckFailed: 'Ponovno preverjanje ni uspelo.',
    rerunRunning: 'Zaganjam ...',
    rerunTitle: 'Ponovno analiziraj z obstoječim prepisom (brez ponovne transkripcije)',
    rerunConfirm: 'Ponovna analiza uporabi obstoječi prepis (brez prenosa in transkripcije) in ustvari NOV vnos. Nadaljujem?',
    rerunFailed: 'Ponovna analiza ni uspela',
    rerunFullFallbackConfirm: 'Prepisa te analize ni več na disku. Poženem POLNO analizo (prenos + transkripcija) iz shranjenega YouTube URL? To porabi 1 kredit.',
    rerunNoUrl: 'Prepis ni na voljo in debata nima YouTube URL — naloži posnetek prek "Analiziraj" in ga ponovno obdelaj.',
    rerunLangTitle: 'Jezik analize',
    rerunStartBtn: 'Poženi',
    rerunCancelBtn: 'Prekliči',
    argExchangeFlow: 'Potek izmenjave',
    argOpponentRebuttal: 'Odbitev nasprotnika',
    argNotAddressed: 'Nasprotnik ni odgovoril na ta argument',
    argOpponentResponse: 'Odziv nasprotnika',
    argDefense: 'Obramba argumenta',
    argRebuttalBy: 'Odbitev',
    argUserAdded: 'ročno dodano',
    argResponse: 'Odziv',
    argDebatablePoints: 'Sporne točke',
    fallacyAdd: 'Dodaj spregledano zmoto',
    fallacyPickType: 'Izberi vrsto zmote…',
    fallacyQuotePlaceholder: 'Dobesedni citat iz prepisa, na katerem zmota temelji',
    fallacyAddSave: 'Dodaj',
    fallacyAddHint: 'Kategorijo (formalna, neformalna, šibko sklepanje) določi ime zmote — enako kot pri samodejni zaznavi.',
    fallacyDeleteTitle: 'Odstrani to zmoto',
    fallacyDeleteConfirm: 'Res odstranim to zmoto iz analize?',
    fallacyRetypeTitle: 'Popravi vrsto zmote',
    argFallacies: 'Zmote',
    argFactCheck: 'Preverjanje dejstev',
    argClose: 'Zapri',
    reviewPrompt: 'Presoja:',
    reviewConfirm: 'Drži',
    reviewDismiss: 'Ne drži',

    // SpeakerTimeline
    stPosition: 'Pozicija',
    stConclusions: 'Zaključki',
    stEvasions: 'Izogibanja',
    stUnsupportedClaims: 'Nepodprte trditve',

    // Tooltip opisi (kratki)
  },
  en: {
    // Nav
    myAnalyses: 'My Analyses',
    newAnalysis: 'New Analysis',
    login: 'Login',
    register: 'Register',
    logout: 'Logout',

    // HomePage
    completedAnalyses: 'Completed Analyses',
    analysisCount: (n) => `${n} ${n === 1 ? 'analysis' : 'analyses'} in database`,
    searchPlaceholder: 'Search by topic, speakers...',
    search: 'Search',
    noAnalyses: 'No saved analyses',
    startFirst: 'Start first analysis',
    all: 'All',
    soloOnly: 'Solo',
    debateOnly: 'Debate',
    noTopic: 'No topic',
    speakers: 'Speakers',
    processing: 'processing',
    deleteConfirm: 'Are you sure you want to delete this analysis?',
    delete: 'Delete',

    // DebateViewPage
    back: 'Back',
    analysis: 'Analysis',
    whatDoesAnalysisMean: 'What does the analysis mean?',
    youtubeVideo: 'YouTube video',
    timeline: 'Timeline',
    factCheck: 'Fact Check',
    report: 'Report',
    analysisNotFound: 'Analysis not found',
    backToList: 'Back to list',
    // PDF export
    exportPdf: 'Export PDF',
    exporting: 'Exporting…',
    diarizationNote: 'Speakers are auto-detected — if any are mislabeled, correct them via “Edit”.',
    fallacyCat_formal_desc: 'An error in the shape of the inference — recognisable without knowing the subject matter (e.g. affirming the consequent).',
    fallacyCat_informal_desc: 'A context-dependent error: the same move can be perfectly legitimate elsewhere.',
    fallacyCat_weak_reasoning_desc: 'The conclusion follows but is stronger than the evidence licenses — an overreaching step.',
    moderatorTitle: 'Moderator',
    moderatorQuestions: 'questions asked',
    moderatorPressed: 'Pressed harder',
    moderatorShowQuestions: 'Show moderator questions',
    moderatorNotScored: 'The moderator is not a debater: they are never scored and never listed among the speakers. This panel is informational only.',

    // FactCheckPanel
    checked: 'Checked',
    verdictsBySpeaker: 'Verdicts by speaker',
    true_: 'True',
    false_: 'False',
    explanation: 'Explanation',
    context: 'Context',
    sources: 'Sources',
    sourceVerdicts: 'What each source says',
    sourceCount: 'Sources',
    independentDomains: 'Independent domains',

    // Report tab
    reportNotAvailable: 'Report not available',
    claimSpeaker: 'Speaker',
    reportOverview: 'Report Overview',
    totalClaims: 'Total claims',
    trueClaims: 'True',
    falseClaims: 'False',
    partiallyTrue: 'Partially true',
    misleading: 'Misleading',
    unverifiableShort: 'Unverifiable',

    // Verdicts
    TRUE: 'TRUE',
    FALSE: 'FALSE',
    PARTIALLY_TRUE: 'PARTIALLY TRUE',
    MISLEADING: 'MISLEADING',
    UNVERIFIABLE: 'UNVERIFIABLE',

    // LoginPage
    loginTitle: 'Login',
    loginSubtitle: 'Sign in to access your analyses',
    usernameOrEmail: 'Username or email',
    password: 'Password',
    loggingIn: 'Logging in...',
    loginButton: 'Login',
    noAccount: "Don't have an account?",
    loginError: 'Login failed',

    // RegisterPage
    registerTitle: 'Register',
    registerSubtitle: 'Create an account to save your analyses',
    username: 'Username',
    usernamePlaceholder: 'e.g. john123',
    email: 'Email',
    emailPlaceholder: 'john@example.com',
    passwordPlaceholder: 'At least 6 characters',
    registering: 'Registering...',
    createAccount: 'Create account',
    haveAccount: 'Already have an account?',
    registerError: 'Registration failed',

    // AnalyzePage
    newAnalysisTitle: 'New Analysis',
    newAnalysisSubtitle: 'Enter a YouTube URL or upload a video/audio file for analysis',
    source: 'Source',
    youtubeUrl: 'YouTube URL',
    videoLink: 'Link to video',
    uploadFile: 'Upload file',
    fileFormats: 'MP3, MP4, WAV, ...',
    file: 'File',
    clickToSelect: 'Click to select a file',
    debateTitle: 'Debate title',
    optional: 'optional',
    debateTitlePlaceholder: 'e.g. Presidential debate 2024',
    debateTitleHint: 'Name for easier identification. If left blank, the system will use the topic from the analysis.',
    analysisMode: 'Analysis mode',
    solo: 'Solo',
    soloDesc: 'Single speaker — speech, interview, or reaction video',
    debate: 'Debate',
    debateDesc: 'Two debaters — one on one',
    speakerNames: 'Speaker names',
    speakerNamesPlaceholder: 'e.g. John Smith, Jane Doe',
    speakerNamesHint: 'Separate with commas. Replaces SPEAKER_0, SPEAKER_1, ... in transcript. If left blank, the system will auto-identify speakers.',
    analysisLanguage: 'Analysis language',
    slovenian: 'Slovenščina',
    slovenianDesc: 'Analysis in Slovenian',
    english: 'English',
    englishDesc: 'Analysis in English',
    submitting: 'Submitting...',
    startAnalysis: 'Start analysis',
    credits: 'Credits',
    unlimited: 'unlimited',
    noCreditsError: 'No credits available. Contact the administrator.',
    rateLimitError: 'Rate limit exceeded. Please wait 24 hours.',
    submitError: 'Submission failed',
    analysisInProgress: 'Analysis in progress',
    waitForCurrentAnalysis: 'Please wait for the current analysis to finish',
    blockingHint: 'You cannot start a new analysis until the current one finishes. The form will unlock automatically when it completes.',
    currentStep: 'Current step',
    openCurrentAnalysis: 'Open current analysis →',
    probingVideo: 'Checking video length...',
    probeFailedHint: 'Could not detect video length — slider will work manually.',
    minutesShort: 'min',

    // JobStatusPage
    jobStatusTitle: 'Analysis in progress',
    jobIdLabel: 'Job ID',
    jobStepLoading: 'Loading configuration',
    jobStepDownloading: 'Downloading audio',
    jobStepUsing: 'Using uploaded file',
    jobStepTranscribing: 'Transcribing speech',
    jobStepFactChecking: 'Fact-checking claims',
    jobStepAnalyzing: 'Analyzing arguments',
    jobStepDone: 'Complete',
    jobCompletedRedirect: 'Analysis complete! Redirecting...',
    jobFailedDefault: 'Analysis failed',
    jobNotFound: 'Analysis not found. It may have expired or been deleted.',
    jobNetworkError: 'Connection to the server was lost. Refresh the page.',
    jobRetry: 'Try again',
    jobQueued: 'Queued...',
    jobProcessing: 'Processing...',
    analysisRunning: 'Analysis running',
    clickToView: 'Click to view',

    // TimeRangeSlider
    trimClip: 'Trim clip',
    resetTrim: 'Reset',
    trimEnd: 'end',
    trimLength: 'Length',
    trimLengthLabel: 'Length:',

    // DebateViewPage
    editDebate: 'Edit',
    editDebateTitle: 'Edit names, arguments, summary',

    // HomePage errors
    loadDebatesError: 'Failed to load analyses. Please try again.',
    deleteDebateError: 'Failed to delete analysis.',

    // Error boundary
    errorBoundaryTitle: 'Something went wrong',
    errorBoundaryMessage: 'Reload the page or try again later.',
    errorBoundaryReload: 'Reload',

    // HomePage tutorial
    tutorialButton: 'How does it work?',
    tutorialTitle: 'How to use Debate Analyzer',
    tutorialClose: 'Close',
    tutorialStep1Title: '1. Submit a recording',
    tutorialStep1Text: 'Enter a YouTube URL or upload an audio/video file. Choose analysis mode (solo or debate) and language.',
    tutorialStep2Title: '2. Wait for analysis',
    tutorialStep2Text: 'The system will transcribe the recording, identify speakers, extract arguments, and verify factual claims using academic sources.',
    tutorialStep3Title: '3. Review results',
    tutorialStep3Text: 'Open the analysis and review the argument timeline, fact-checking with sources, and the full report with verdicts for each claim.',

    // AppGuideModal
    guideCloseLabel: 'Close help',
    guideHowItWorks: 'Guide',
    guideTitle: 'How to read this analysis',
    guideClose: 'Close',

    // 1. What the app does
    guideWhatAppDoes: 'What the app does',
    guideWhatAppDoesText: 'Extracts main arguments, premises, and counterarguments from a recording or transcript, verifies factual claims, and (for debates) compares speakers. The result is not absolute truth — it is a structured analysis you can edit if needed.',

    // 2. How to read arguments
    guideHowToReadNodes: 'How to read arguments',
    guideNodeLine1: 'Each card is one argument — a complete chain of reasoning (premises → reasoning → conclusion).',
    guideNodeLine2: 'When you open it you see premises, the opponent response, and related fact-checks.',
    guideNodeLine3: 'Color and emphasis indicate where an argument is strong or problematic.',

    // 3. Fact-check verdicts
    guideVerdictsTitle: 'Fact-check verdicts',
    guideVerdictTrue: 'True',
    guideVerdictTrueDesc: 'Claim is verifiably accurate against reliable sources.',
    guideVerdictPartial: 'Partially true',
    guideVerdictPartialDesc: 'Mostly accurate with minor inaccuracies or caveats.',
    guideVerdictMisleading: 'Misleading',
    guideVerdictMisleadingDesc: 'Contains a kernel of truth but framed in a way that distorts the impression.',
    guideVerdictFalse: 'False',
    guideVerdictFalseDesc: 'Claim contradicts reliable evidence.',
    guideVerdictUnverifiable: 'Unverifiable',
    guideVerdictUnverifiableDesc: 'Not enough reliable sources to judge (opinion, prediction, debate position).',

    // 4. Source perspectives
    guideSourcesTitle: 'Balanced sources',
    guideSourcesDesc: 'For every claim we look for sources from different perspectives. If someone defends a Catholic position, we also consult Catholic sources — not only opposing ones.',
    guideSourceAligned: "From speaker's tradition",
    guideSourceNeutral: 'Independent / mainstream',
    guideSourceOpposing: 'From opposing camp',

    // 5. Analysis modes
    guideModesTitle: 'Analysis modes',
    guideModeSoloTitle: 'Solo',
    guideModeSoloDesc: 'One primary speaker — speech, interview, lecture, or reaction video.',
    guideModeDebateTitle: 'Debate',
    guideModeDebateDesc: 'One-on-one debate: exactly two debaters with opposing positions. A moderator does not count as a debater — they are recorded separately and never scored. If the recording contains more than two debaters, the analysis stops with a warning.',

    // 6. Edit
    guideEditTitle: 'You can edit the analysis',
    guideEditDesc: 'Click "Edit" in the top right of the analysis page. You can rename speakers, edit or delete arguments, add new ones, edit the summary and topic.',

    // Footer disclaimer
    guideImportantNote: 'Good to know',
    guideImportantNoteText: 'The analysis is AI-generated and may contain mistakes. Verify important claims yourself, fix arguments via "Edit", and do not take any score as a final judgment.',

    // ArgumentNode
    argSpeaker: 'Speaker',
    argCloseLabel: 'Close argument details',
    argPremises: 'Premises',
    argDerived: 'Derived argument (conclusion)',
    rerun: 'Re-run analysis',
    recheck: 'Refresh sources',
    recheckRunning: 'Checking …',
    recheckTitle: 'Re-check the facts over the same analysis. Arguments, fallacies and rebuttals stay; sources and verdicts are refreshed.',
    recheckConfirm: 'Re-check the facts of this analysis? The arguments stay as they are, only the sources and verdicts are refreshed. Costs one credit.',
    recheckFailed: 'Re-check failed.',
    rerunRunning: 'Starting ...',
    rerunTitle: 'Re-analyze using the existing transcript (no re-transcription)',
    rerunConfirm: 'Re-running uses the existing transcript (no download or transcription) and creates a NEW entry. Continue?',
    rerunFailed: 'Re-run failed',
    rerunFullFallbackConfirm: 'The transcript for this analysis is no longer on disk. Run a FULL analysis (download + transcription) from the saved YouTube URL? This uses 1 credit.',
    rerunNoUrl: 'Transcript unavailable and this debate has no YouTube URL — upload the recording via "Analyze" to process it again.',
    rerunLangTitle: 'Analysis language',
    rerunStartBtn: 'Run',
    rerunCancelBtn: 'Cancel',
    argExchangeFlow: 'Exchange flow',
    argOpponentRebuttal: 'Opponent rebuttal',
    argNotAddressed: 'Opponent did not address this argument',
    argOpponentResponse: 'Opponent response',
    argDefense: 'Argument defense',
    argRebuttalBy: 'Rebuttal',
    argUserAdded: 'manually added',
    argResponse: 'Response',
    argDebatablePoints: 'Debatable points',
    fallacyAdd: 'Add a missed fallacy',
    fallacyPickType: 'Pick a fallacy type…',
    fallacyQuotePlaceholder: 'Verbatim quote from the transcript the fallacy rests on',
    fallacyAddSave: 'Add',
    fallacyAddHint: 'The category (formal, informal, weak reasoning) follows from the name — the same rule as for automatic detection.',
    fallacyDeleteTitle: 'Remove this fallacy',
    fallacyDeleteConfirm: 'Remove this fallacy from the analysis?',
    fallacyRetypeTitle: 'Correct the fallacy type',
    argFallacies: 'Fallacies',
    argFactCheck: 'Fact check',
    argClose: 'Close',
    reviewPrompt: 'Your verdict:',
    reviewConfirm: 'Correct',
    reviewDismiss: 'Not correct',

    // SpeakerTimeline
    stPosition: 'Position',
    stConclusions: 'Conclusions',
    stEvasions: 'Evasions',
    stUnsupportedClaims: 'Unsupported claims',

    // Tooltip descriptions (short)
  },
}

const LanguageContext = createContext()

/**
 * Human-readable name for a categorical value the model returned.
 *
 *   tv('fallacy', 'straw_man')  → 'slamnati mož'
 *
 * Reads the SAME file as the Python back end (enumLabels.json), so the
 * interface, the text report and the PDF cannot drift apart. Values stay
 * English everywhere in the data — only the display is translated.
 * An unknown value degrades to itself with underscores turned into spaces.
 */
function makeTv(lang) {
  return function tv(group, value) {
    if (value === null || value === undefined) return ''
    const raw = String(value).trim()
    if (!raw) return ''
    const g = enumLabels[group] || {}
    let entry = g[raw]
    if (!entry) {
      const hit = Object.keys(g).find(k => k.toLowerCase() === raw.toLowerCase())
      if (hit) entry = g[hit]
    }
    if (!entry) return raw.replace(/_/g, ' ')
    return entry[lang] || entry.en || raw.replace(/_/g, ' ')
  }
}

export function LanguageProvider({ children }) {
  const [lang, setLang] = useState('sl')
  const t = translations[lang]
  const tv = makeTv(lang)
  return (
    <LanguageContext.Provider value={{ lang, setLang, t, tv }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLanguage() {
  return useContext(LanguageContext)
}
