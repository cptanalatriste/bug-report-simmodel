"""
This modules is used to gather payoff values needed for equilibrium calculation. Now it is also capable of triggering
gambit and calculating the equilibrium.
"""

import time
import sys
import winsound
import copy
import collections

import pandas as pd

import itertools
from sklearn.cluster import KMeans

import simmodel
import simtwins
import simdata
import simdriver
import simutils
import simcruncher
import gtutils

DEFAULT_CONFIGURATION = {
    # General game configuration
    'REPLICATIONS_PER_PROFILE': 30,  # On the Walsh paper, they do 2500 replications per profile.

    # Payoff function parameters
    'PRIORITY_SCORING': True,
    'SCORE_MAP': {
        simdata.NON_SEVERE_PRIORITY: 10,
        simdata.NORMAL_PRIORITY: 10 * 2,
        simdata.SEVERE_PRIORITY: 10 * 5
    },
    'N_PLAYERS': None,  # None, for not filtering among suitable players.
    'CLONE_PLAYER': 0,  # None to disable cloning
    'CLONE_MEDIAN': True,

    # Throtling configuration parameters.
    'THROTTLING_ENABLED': True,
    'INFLATION_FACTOR': 1,

    # Empirical Strategies parameters.
    'EMPIRICAL_STRATEGIES': False,
    'N_CLUSTERS': 5,

    'HEURISTIC_STRATEGIES': True,

    # Gatekeeper configuration.
    # 'GATEKEEPER_CONFIG': {'review_time': 8,  # False to disable the Gatekeeper on the simulation.
    #                       'capacity': 1},
    'GATEKEEPER_CONFIG': False,

    # Team Configuration
    'NUMBER_OF_TEAMS': 2,
    'AGGREGATE_AGENT_TEAM': -1
}


def select_game_players(reporter_configuration, number_of_players=5, clone_player=None):
    """
    Selects which of the players available will be the ones playing the game.
    :param clone_player: The index of the players. whose clones will be returned.
    :param number_of_players: Number of players to be playing the game. In case it is None, no filtering will be performed.
    :param reporter_configuration: List of non drive-by testers.
    :return: List of selected players.
    """
    sorted_reporters = sorted(reporter_configuration,
                              key=lambda config: len(config['interarrival_time_gen'].observations), reverse=True)

    if number_of_players is None:
        number_of_players = len(reporter_configuration)

    if clone_player is not None:
        source_player = [sorted_reporters[clone_player]]
        source_name = source_player[0]['name']

        print "Cloning player at index: ", clone_player, "(", source_name, ")"
        simdriver.fit_reporter_distributions(source_player)

        list_with_clones = []

        for index in range(number_of_players):
            clone = copy.deepcopy(source_player[0])
            clone['name'] = 'clone_' + source_name + '_' + str(index)
            list_with_clones.append(clone)

        return list_with_clones

    return sorted_reporters[:number_of_players]


def select_reporters_for_simulation(reporter_configuration):
    """
    The production of these reporters will be considered for simulation extraction parameters
    :param reporter_configuration: All valid reporters.
    :return: A filtered list of reporters.
    """

    reporters_with_corrections = [config for config in reporter_configuration if
                                  config['with_modified_priority'] > 0]

    print "Original Reporters: ", len(reporter_configuration), " Reporters with corrected priorities: ", len(
        reporters_with_corrections)

    return reporters_with_corrections


def get_heuristic_strategies():
    """
    Returns a set of strategies not related with how users are behaving in data.
    :return:
    """

    honest_strategy = simmodel.EmpiricalInflationStrategy(strategy_config={'name': simmodel.HONEST_STRATEGY,
                                                                           simutils.NONSEVERE_CORRECTION_COLUMN: 0.0,
                                                                           simutils.SEVERE_CORRECTION_COLUMN: 0.0})

    simple_inflate_strategy = simmodel.EmpiricalInflationStrategy(
        strategy_config={'name': simmodel.SIMPLE_INFLATE_STRATEGY,
                         simutils.NONSEVERE_CORRECTION_COLUMN: 1.0,
                         simutils.NON_SEVERE_INFLATED_COLUMN: 1.0,
                         simutils.SEVERE_CORRECTION_COLUMN: 0.0})

    return [honest_strategy,
            simple_inflate_strategy]


def get_empirical_strategies(reporter_configuration, n_clusters=3):
    """
    It will group a list of reporters in a predefined number of clusters
    :param n_clusters: Number of clusters, which will determine the number of strategies to extract.
    :param reporter_configuration: List of reporter configuration
    :return: The representative strategy per team.
    """

    print "Gathering strategies from reporter behaviour ..."
    print "Original number of reporters: ", len(reporter_configuration)

    reporters_with_corrections = [config for config in reporter_configuration if
                                  config['with_modified_priority'] > 0]
    print "Reporters with corrections: ", len(reporters_with_corrections)

    correction_dataframe = simutils.get_reporter_behavior_dataframe(reporters_with_corrections)
    reporter_dataframe = simutils.get_reporter_behavior_dataframe(reporter_configuration)

    kmeans = KMeans(n_clusters=n_clusters,
                    init='k-means++',
                    n_init=10,
                    max_iter=300,
                    random_state=0)

    kmeans.fit(correction_dataframe)

    predicted_clusters = kmeans.predict(reporter_dataframe)
    cluster_column = 'cluster'
    reporter_dataframe[cluster_column] = predicted_clusters

    centroids = kmeans.cluster_centers_
    print "Clustering centroids ..."

    strategies_per_team = []
    for index, centroid in enumerate(centroids):
        nonsevere_correction_index = 3
        severe_correction_index = 4
        nonsevere_inflation_index = 0
        severe_deflation_index = 1

        print  " ", simutils.REPORTER_COLUMNS[nonsevere_inflation_index], ": ", "{0:.0f}%".format(
            centroid[nonsevere_inflation_index] * 100), \
            " ", simutils.REPORTER_COLUMNS[severe_deflation_index], ": ", "{0:.0f}%".format(
            centroid[severe_deflation_index] * 100), \
            " ", simutils.REPORTER_COLUMNS[2], ": ", "{0:.0f}%".format(centroid[2] * 100), \
            " ", simutils.REPORTER_COLUMNS[nonsevere_correction_index], ": ", "{0:.0f}%".format(
            centroid[nonsevere_correction_index] * 100), \
            " ", simutils.REPORTER_COLUMNS[severe_correction_index], ": ", "{0:.0f}%".format(
            centroid[severe_correction_index] * 100)

        strategies_per_team.append({'name': 'EMPIRICAL' + str(index),
                                    simutils.NONSEVERE_CORRECTION_COLUMN: centroid[nonsevere_correction_index],
                                    simutils.SEVERE_CORRECTION_COLUMN: centroid[severe_correction_index],
                                    simutils.NON_SEVERE_INFLATED_COLUMN: centroid[nonsevere_inflation_index],
                                    simutils.SEVERE_DEFLATED_COLUMN: centroid[severe_deflation_index]
                                    })

    print "Cluster distribution: \n", reporter_dataframe[cluster_column].value_counts()

    for index, cluster in enumerate(reporter_dataframe[cluster_column].values):
        reporter_configuration[index][cluster_column] = cluster_column

    return strategies_per_team


def get_strategy_map(strategy_list, teams):
    """
    Creates a strategy map, with all the possible strategy profiles on the game.
    :return: A map with all the possible strategy profiles according the players and strategies available.
    """
    strategy_maps = []
    strategy_profiles = list(itertools.product(strategy_list, repeat=teams))

    for profile in strategy_profiles:
        strategy_map = {'name': '',
                        'map': {}}

        # To keep the order preferred by Gambit
        for index, strategy in enumerate(reversed(list(profile))):
            strategy_name = strategy.name

            strategy_map['name'] += strategy_name + "_"
            strategy_map['map'][index] = strategy

        strategy_map['name'] = strategy_map['name'][:-1]
        strategy_maps.append(strategy_map)

    return strategy_maps


def configure_strategies_per_team(player_configuration, strategy_map):
    """
    Assigns the strategies corresponding to teams according to an specific strategy profile.
    :return: Player index whose payoff value is of interest.
    """

    for config in player_configuration:
        config['strategy'] = strategy_map[config['team']]


def start_payoff_calculation(enhanced_dataframe, project_keys, game_configuration):
    """
    Given a strategy profile list, calculates payoffs per player thorugh simulation.
    :param enhanced_dataframe: Report data to gather simulation input.
    :param project_keys: Projects to be considered.
    :return: Payoffs per player per profile.
    """

    input_params = prepare_simulation_inputs(enhanced_dataframe, project_keys, game_configuration)

    return run_simulation(strategy_maps=input_params.strategy_maps, strategies_catalog=input_params.strategies_catalog,
                          player_configuration=input_params.player_configuration,
                          dev_team_size=input_params.dev_team_size,
                          bugs_by_priority=input_params.bugs_by_priority,
                          resolution_time_gen=input_params.resolution_time_gen,
                          dev_team_bandwith=input_params.dev_team_bandwith, teams=input_params.teams,
                          game_configuration=game_configuration)


def prepare_simulation_inputs(enhanced_dataframe, project_keys, game_configuration):
    """
    Based on the provided dataframe, this functions produces the simulation inputs.

    :param enhanced_dataframe: Dataframe with bug reports.
    :param project_keys: Selected projects.
    :param game_configuration: Game configuration.
    :return: Simulation inputs
    """
    print "Starting simulation on projects ", project_keys
    total_projects = len(project_keys)

    if game_configuration["PROJECT_FILTER"] is not None and len(game_configuration["PROJECT_FILTER"]) >= 1:
        project_keys = game_configuration["PROJECT_FILTER"]

    print "Original projects ", total_projects, "Project Filter: ", game_configuration["PROJECT_FILTER"], \
        " Projects remaining after reduction: ", len(project_keys)

    valid_reports = simdriver.get_valid_reports(project_keys, enhanced_dataframe)
    valid_reporters = simdriver.get_reporter_configuration(valid_reports)
    print "Reporters after drive-in tester removal ...", len(valid_reporters)

    print "Generating elbow-method plot..."
    simutils.elbow_method_for_reporters(valid_reporters, file_prefix="_".join(project_keys))

    empirical_strategies = [simmodel.EmpiricalInflationStrategy(strategy_config=strategy_config) for strategy_config in
                            get_empirical_strategies(valid_reporters, n_clusters=game_configuration["N_CLUSTERS"])]

    strategies_catalog = []

    if game_configuration["EMPIRICAL_STRATEGIES"]:
        strategies_catalog.extend(empirical_strategies)

    if game_configuration["HEURISTIC_STRATEGIES"]:
        strategies_catalog.extend(get_heuristic_strategies())

    print "start_payoff_calculation->strategies_catalog: ", strategies_catalog

    # This are the reporters whose reported bugs will be used to configure the simulation.
    reporter_configuration = select_reporters_for_simulation(valid_reporters)

    # This is the configuration of the actual game players.
    clone_player = game_configuration["CLONE_PLAYER"]
    if game_configuration["CLONE_MEDIAN"]:
        clone_player = len(reporter_configuration) / 2

    player_configuration = select_game_players(reporter_configuration,
                                               number_of_players=game_configuration["N_PLAYERS"],
                                               clone_player=clone_player)

    print "Reporters selected for playing the game ", len(player_configuration)

    if clone_player is None:
        # When cloning is configured, distribution fitting happened before.
        simdriver.fit_reporter_distributions(player_configuration)

    teams = game_configuration["NUMBER_OF_TEAMS"]
    strategy_maps = get_strategy_map(strategies_catalog, teams)

    engaged_testers = [reporter_config['name'] for reporter_config in reporter_configuration]
    valid_reports = simdata.filter_by_reporter(valid_reports, engaged_testers)
    print "Issues in training after reporter filtering: ", len(valid_reports.index)

    print "Starting simulation for project ", project_keys

    bugs_by_priority = {index: value
                        for index, value in
                        valid_reports[simdata.SIMPLE_PRIORITY_COLUMN].value_counts().iteritems()}

    resolution_time_gen = simdriver.get_simulation_input(training_issues=valid_reports)
    dev_team_size, issues_resolved, resolved_in_period, dev_team_bandwith = simdriver.get_dev_team_production(
        valid_reports)

    bug_reporters = valid_reports['Reported By']
    test_team_size = bug_reporters.nunique()

    print "Project ", project_keys, " Test Period: ", "ALL", " Reporters: ", test_team_size, " Developers:", dev_team_size, \
        " Reports: ", bugs_by_priority, " Resolved in Period: ", issues_resolved, " Dev Team Bandwith: ", dev_team_bandwith

    input_params = collections.namedtuple('SimulationParams',
                                          ['strategy_maps', 'strategies_catalog', 'player_configuration',
                                           'dev_team_size', 'bugs_by_priority', 'resolution_time_gen',
                                           'dev_team_bandwith', 'teams'])

    return input_params(strategy_maps, strategies_catalog, player_configuration, dev_team_size, bugs_by_priority,
                        resolution_time_gen, dev_team_bandwith, teams)


def run_simulation(strategy_maps, strategies_catalog, player_configuration, dev_team_size, bugs_by_priority,
                   resolution_time_gen,
                   dev_team_bandwith, teams, game_configuration):
    """

    :param strategy_maps: Strategy profiles of the game.
    :param strategies_catalog: List of strategies available for players.
    :param player_configuration: List of reporters with parameters.
    :param dev_team_size: Number of developers available for bug fixing.
    :param bugs_by_priority: Bugs to find in the system, according to their priority.
    :param resolution_time_gen: Generators for resolution time. Per priority.
    :param dev_team_bandwith: Number of dev time hours for bug fixing.
    :param teams: Number of teams available.
    :param game_configuration: Game configuration parameters.
    :return: List of equilibrium profiles.
    """

    simulation_time = sys.maxint

    profile_payoffs = []

    print "Simulation configuration: REPLICATIONS_PER_PROFILE ", game_configuration[
        "REPLICATIONS_PER_PROFILE"], " THROTTLING_ENABLED ", \
        game_configuration["THROTTLING_ENABLED"], " PRIORITY_SCORING ", game_configuration["PRIORITY_SCORING"], \
        " PROJECT_FILTER ", game_configuration["PROJECT_FILTER"], " EMPIRICAL_STRATEGIES ", game_configuration[
        "EMPIRICAL_STRATEGIES"], \
        " HEURISTIC_STRATEGIES ", game_configuration["HEURISTIC_STRATEGIES"], " N_CLUSTERS ", game_configuration[
        "N_CLUSTERS"], " N_PLAYERS ", game_configuration["N_PLAYERS"], \
        " CLONE_PLAYER ", game_configuration["CLONE_PLAYER"], " CLONE_MEDIAN ", game_configuration[
        "CLONE_MEDIAN"], " GATEKEEPER_CONFIG ", game_configuration["GATEKEEPER_CONFIG"], ' INFLATION_FACTOR ', \
        game_configuration['INFLATION_FACTOR']

    print "Simulating ", len(strategy_maps), " strategy profiles..."

    simulation_history = []
    for index, map_info in enumerate(strategy_maps):
        file_prefix, strategy_map = map_info['name'], map_info['map']

        overall_dataframes = []

        for team, strategy in strategy_map.iteritems():
            print "Getting payoff for team ", team, " on profile ", file_prefix
            simtwins.aggregate_players(team, player_configuration, game_configuration["AGGREGATE_AGENT_TEAM"])
            twins_strategy_map = simtwins.get_twins_strategy_map(team, strategy_map,
                                                                 game_configuration["AGGREGATE_AGENT_TEAM"])

            configure_strategies_per_team(player_configuration, twins_strategy_map)
            overall_dataframe = simtwins.check_simulation_history(simulation_history, player_configuration,
                                                                  game_configuration["AGGREGATE_AGENT_TEAM"])

            if overall_dataframe is None:
                simulation_output = simutils.launch_simulation(
                    team_capacity=dev_team_size,
                    bugs_by_priority=bugs_by_priority,
                    reporters_config=player_configuration,
                    resolution_time_gen=resolution_time_gen,
                    max_time=simulation_time,
                    max_iterations=game_configuration["REPLICATIONS_PER_PROFILE"],
                    dev_team_bandwidth=dev_team_bandwith,
                    inflation_factor=game_configuration["INFLATION_FACTOR"],
                    quota_system=game_configuration["THROTTLING_ENABLED"],
                    gatekeeper_config=game_configuration["GATEKEEPER_CONFIG"])

                # simulation_output = simutils.launch_simulation_parallel(
                #     team_capacity=dev_team_size,
                #     bugs_by_priority=bugs_by_priority,
                #     reporters_config=player_configuration,
                #     resolution_time_gen=resolution_time_gen,
                #     max_time=simulation_time,
                #     max_iterations=game_configuration["REPLICATIONS_PER_PROFILE"],
                #     dev_team_bandwidth=dev_team_bandwith,
                #     inflation_factor=game_configuration["INFLATION_FACTOR"],
                #     quota_system=game_configuration["THROTTLING_ENABLED"],
                #     gatekeeper_config=game_configuration["GATEKEEPER_CONFIG"])

                simulation_result = simcruncher.consolidate_payoff_results("ALL", player_configuration,
                                                                           simulation_output["completed_per_reporter"],
                                                                           simulation_output["bugs_per_reporter"],
                                                                           simulation_output["reports_per_reporter"],
                                                                           simulation_output["resolved_per_reporter"],
                                                                           game_configuration["SCORE_MAP"],
                                                                           game_configuration["PRIORITY_SCORING"])
                overall_dataframe = pd.DataFrame(simulation_result)
                simulation_history.append(overall_dataframe)

            else:
                print "Profile ", twins_strategy_map, " has being already executed. Recycling dataframe."

            overall_dataframe.to_csv("csv/agent_team_" + str(team) + "_" + file_prefix + '_simulation_results.csv',
                                     index=False)
            overall_dataframes.append(overall_dataframe)

        payoffs = simcruncher.get_team_metrics(str(index) + "-" + file_prefix, "ALL", teams, overall_dataframes,
                                               game_configuration["NUMBER_OF_TEAMS"])
        profile_payoffs.append((file_prefix, payoffs))

    game_desc = "AS-IS" if not game_configuration["THROTTLING_ENABLED"] else "THROTTLING"
    game_desc = "GATEKEEPER" if game_configuration["GATEKEEPER_CONFIG"] else game_desc

    print "Generating Gambit NFG file ..."
    gambit_file = gtutils.get_strategic_game_format(game_desc, player_configuration, strategies_catalog,
                                                    profile_payoffs, teams)

    print "Executing Gambit for equilibrium calculation..."
    equilibrium_list = gtutils.calculate_equilibrium(strategies_catalog, gambit_file)

    print "Equilibria found: ", len(equilibrium_list), equilibrium_list
    return equilibrium_list


def main():
    print "Loading information from ", simdata.ALL_ISSUES_CSV
    all_issues = pd.read_csv(simdata.ALL_ISSUES_CSV)

    print "Adding calculated fields..."
    enhanced_dataframe = simdata.enhace_report_dataframe(all_issues)

    valid_projects = simdriver.get_valid_projects(enhanced_dataframe)
    start_payoff_calculation(enhanced_dataframe, valid_projects, DEFAULT_CONFIGURATION)


if __name__ == "__main__":

    start_time = time.time()
    try:
        main()
    finally:
        winsound.Beep(2500, 1000)

    print "Execution time in seconds: ", (time.time() - start_time)
