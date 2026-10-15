# 🛡️ Relatório de Conformidade com a LGPD (Lei Federal nº 13.709/2018)

> **Projeto:** Vigilance-Drop Detection With Single-Channel Consumer EEG — Edge Inference And FHIR-Native Observations  
> **Contexto Acadêmico:** Mestrado em Computação Aplicada — UNISINOS (Linha de Computação Aplicada à Saúde)  
> **Classificação dos Dados:** Dados Pessoais Sensíveis de Saúde (Art. 5º, II da LGPD)  

---

## 1. Enquadramento Legal e Base de Tratamento

Sinais eletroencefalográficos (EEG), índices de fadiga e probabilidades de rebaixamento de vigilância constituem **dados biomédicos referentes à saúde**, enquadrados como **Dados Pessoais Sensíveis** segundo o **Artigo 5º, inciso II da LGPD**.

O tratamento desses dados nesta arquitetura de software fundamenta-se nas seguintes bases legais:

1. **Art. 11, inciso II, alínea "c" (Realização de estudos por órgão de pesquisa):**
   * Autoriza expressamente o tratamento de dados pessoais sensíveis para fins acadêmicos e científicos, *garantida, sempre que possível, a anonimização e a pseudonimização dos dados*.
2. **Art. 11, inciso II, alínea "f" (Tutela da saúde):**
   * Aplicação em procedimentos com padrões técnicos que visam resguardar a integridade física e segurança de operadores/motoristas contra acidentes decorrentes de sonolência extrema.

---

## 2. Princípios da LGPD Atendidos na Arquitetura (Art. 6º)

| Princípio | Artigo | Como a Arquitetura Cumpre |
|---|---|---|
| **Finalidade** | Art. 6º, I | O sistema processa telemetria com propósito legítimo, específico e explícito: detecção de rebaixamento de vigilância em tempo real. |
| **Adequação** | Art. 6º, II | Os dados coletados (frequências espectrais alfa e teta) são estritamente compatíveis com o estado da arte em neurociência para monitoramento de vigília. |
| **Necessidade (Minimização)** | Art. 6º, III | **Nenhum dado biográfico** (como Nome, CPF, Telefone, Endereço ou Foto) é coletado ou transmitido. O pipeline extrai apenas features físicas agregadas. |
| **Livre Acesso & Transparência** | Art. 6º, IV e VI | O painel Streamlit e a persistência local FHIR permitem consulta direta e transparente aos dados registrados da sessão. |
| **Segurança & Prevenção** | Art. 6º, VII e VIII | *Write-ahead logging* em SQLite local contra perda de dados; tagueamento de segurança HL7 FHIR `meta.security` com confidencialidade `R` (Restricted). |
| **Não-Discriminação** | Art. 6º, IX | **Proibição expressa por design:** o sistema não calcula "score cerebral", "ranking de produtividade", "idade biológica" ou comparações entre indivíduos. |
| **Responsabilização** | Art. 6º, X | Trilha de auditoria completa gravada com carimbo de tempo UTC e rastreabilidade no SQLite local. |

---

## 3. Salvaguardas Técnicas Implementadas (*Privacy by Design*)

### 3.1 Pseudonimização Obrigatória na Ingestão (Art. 13 & Art. 5º, XI)
* Módulo: [`src/privacy.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/privacy.py)
* Função `pseudonymize_identifier()`:
  * Detecta e bloqueia identificadores diretos (ex: e-mails, CPFs, nomes compostos).
  * Converte identificadores em hashes irreversíveis criptográficos com salt (`anon-<digest>`).
  * Garante que nenhum PII direto trafegue em tópicos MQTT (`study/{user_id}/eeg/observation`) ou em recursos FHIR.

### 3.2 Tagueamento de Confidencialidade HL7 FHIR R4 (Art. 46)
* Módulo: [`src/fhir_builder.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/fhir_builder.py)
* Todas as observações construídas incluem o elemento `meta.security`:
  ```json
  "meta": {
    "security": [
      {
        "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
        "code": "R",
        "display": "Restricted"
      }
    ]
  }
  ```
  Isso instrui qualquer barramento interoperável hospitalar (EHR/EMR) downstream que o registro exige controle estrito de acesso.

### 3.3 Direito do Titular à Eliminação dos Dados (Art. 18, inciso VI)
* Módulo: [`src/local_store.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/local_store.py)
* Método `delete_subject_data(subject_id: str) -> int`:
  * Permite ao controlador expurgar instantaneamente todas as observações do banco local caso o titular solicite revogação do consentimento ou eliminação da sua base.

### 3.4 Limitação de Armazenamento no Edge Gateway (Art. 16)
* Módulo: [`src/local_store.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/local_store.py)
* Método `purge_old_published_observations(retention_days=7) -> int`:
  * Remove automaticamente registros antigos que já foram confirmados e publicados via MQTT.
  * Protege a memória física do dispositivo edge contra invasões ou furtos, reduzindo a superfície de exposição de dados a longo prazo.
  * **Garantia de Não-Perda:** Observações que falharam na publicação (`mqtt_published = 0`) **nunca** são expurgadas antes de serem devidamente sincronizadas.

---

## 4. Recomendações para Implantação em Produção

Para transição do ambiente de laboratório para produção clínica ou industrial:
1. **Comunicação Segura (MQTTS):** Habilitar TLS v1.3 com criptografia AES-256 e certificados mútuos (mTLS) na porta 8883 do Mosquitto.
2. **Criptografia em Repouso:** Empregar *SQLCipher* ou criptografia de volume no sistema operacional do gateway edge (BitLocker / LUKS) para o arquivo `fhir_observations.db`.
3. **Termo de Consentimento Livre e Esclarecido (TCLE):** Para sujeitos de pesquisa participantes dos testes de bancada.
