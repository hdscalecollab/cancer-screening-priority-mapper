library(tidyverse)


setwd("~/nci_data_jamboree/MVP_data/")

acs5_2019_2023_tract_MVP <- read.csv("~/nci_data_jamboree/MVP_data/acs5_2019_2023_tract_MVP v2.csv")


df_clean <- acs5_2019_2023_tract_MVP %>%
  separate_wider_delim(
    cols = NAME, 
    delim = ";", 
    names = c("census_tract", "County", "State")
     
  )

df_clean <- df_clean %>%
  select(!ends_with("M")) %>%
  mutate(FIPS = str_sub(GEOID, 1, 5))

df_clean_w_calcs <- df_clean %>%
  transmute(
    FIPS = FIPS,
    GEOID20 = GEOID,
    county = County,
    state = State,
    acs_period = acs_period,
    number_of_women_40_74 = rowSums(pick(B01001_038E:B01001_046E)),
         number_of_women_40_49 = rowSums(pick(B01001_038E:B01001_039E)),
        percent_non_hispanic_white_residents = ((B03002_003E)/(B03002_001E))*100,
    percent_non_hispanic_asian_residents = ((B03002_006E)/(B03002_001E))*100,
         percent_non_hispanic_black_residents = ((B03002_004E)/(B03002_001E))*100,
         percent_hispanic_residents = ((B03002_012E)/(B03002_001E))*100,
         percent_non_hispanic_native_residents = ((B03002_005E)/(B03002_001E))*100,
         a = rowSums(pick(B19001H_014E:B19001H_017E)),
         p = rowSums(pick(B19001B_002E:B19001B_005E)),
         ice = (a-p)/B19001_001E,
         percent_uninsured_women = ((rowSums(pick(c(B27001_045E, B27001_048E, B27001_051E, B27001_054E))))/(rowSums(pick(c(B27001_043E,B27001_046E, B27001_049E, B27001_052E))))*100),
         percent_women_live_below_poverty = (rowSums(pick(c(B17001_026E, B17001_027E, B17001_028E, B17001_029E))))/(rowSums(pick(c(B17001_055E,B17001_056E, B17001_057E, B17001_058E))))*100,
         percent_women_no_high_school_diploma = ((rowSums(pick(B15002_020E:B15002_027E)))/B15002_019E)*100,
         percent_limited_english = ((rowSums(pick(c(C16002_004E, C16002_007E, C16002_010E, C16002_013E))))/C16002_001E)*100,
         percent_households_no_vehicle = ((rowSums(pick(c(B25044_003E, B25044_010E))))/B25044_001E)*100
         
  )


write.csv(df_clean_w_calcs, "~/nci_data_jamboree/MVP_data/df_w_calcs_2.csv")

incidence <- read.csv("~/nci_data_jamboree/MVP_data/Incidence_joined_tracts.csv")[, -1] 
mam_distance <- read.csv("~/nci_data_jamboree/MVP_data/mammography_distance.csv") 

incidence_mam_distance <- merge(incidence, mam_distance, by = "GEOID20")

df_calc_incidence_mam_distance <- merge(df_clean_w_calcs, incidence_mam_distance, by = "GEOID20")

rank_fxn <- function(x) {
  factor(ifelse(x > 66.67, "high", 
         ifelse(66.67 <= x | x >= 33.33, "moderate", "low")), 
         levels = c("low", "moderate", "high"))
}


df_percentiles <- df_calc_incidence_mam_distance %>%
  mutate(across(c(6:25), ~percent_rank(.)*100)
          )

df_rank <- df_percentiles %>%
  transmute(
    FIPS = FIPS,
    geoid = GEOID20,
    county = county,
    state = state,
    acs_period = acs_period,
    across(c(6:25), ~rank_fxn(.)))

write.csv(df_percentiles, "~/nci_data_jamboree/MVP_data/df_percentiles_v2.csv")
write.csv(df_rank, "~/nci_data_jamboree/MVP_data/df_rank_v2.csv")

df_score <- df_rank %>%
  transmute(
    FIPS = FIPS,
    geoid = geoid,
    county = county,
    state = state,
    acs_period = acs_period,
    across(c(6:25), ~sapply(., unclass))) %>%
  mutate(barriers = rowSums(pick(c(percent_uninsured_women, percent_households_no_vehicle,
                                   percent_women_no_high_school_diploma, percent_limited_english))),
         access = NEAR_DIST,
         burden = Percent_late_stage)

write.csv(df_score, "~/nci_data_jamboree/MVP_data/df_score.csv")

df_class <- df_score %>%
  transmute(
    geoid20 = geoid,
    county = county,
    state = state,
    barriers = barriers,
    burden = burden,
    access = access,
    acs_period = acs_period,
    class = case_when( burden > 1 & access > 1 & barriers > 6 ~ "High priority",
                       burden > 1 & access > 1 & barriers < 6 ~ "Access gap",
                       burden > 1 & access == 1 & barriers > 6 ~ "Barrier gap",
                       burden > 1 & access == 1 & barriers < 6 ~ "Monitor",
                       burden = 1 & access >= 1 & barriers >= 1 ~ "Lower priority")
  )
write.csv(df_class, "~/nci_data_jamboree/MVP_data/df_classification.csv")












